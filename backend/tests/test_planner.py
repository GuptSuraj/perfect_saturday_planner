from datetime import timedelta
from unittest.mock import AsyncMock

import httpx
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from graph import build_graph
from language import supported_script
from providers import Providers
from schemas import Preferences, Proposal, Route, today_in_india, upcoming_saturday
from state import PlannerState
from tools import make_tools
from validation import opening_check, validate


def saturday():
    return upcoming_saturday(today_in_india() + timedelta(days=1))


def make_state():
    state = PlannerState(
        publish=AsyncMock(return_value=True),
        preferences=Preferences(
            city="Bengaluru", budget=2000, available_minutes=240,
            dietary="vegetarian_options", target_date=saturday(),
        ),
        places={
            "cafe": {
                "id": "cafe", "_city": "bengaluru", "displayName": {"text": "Fixture Café"},
                "formattedAddress": "Bengaluru, India", "businessStatus": "OPERATIONAL",
                "types": ["restaurant"], "servesVegetarianFood": True,
                "priceRange": {
                    "startPrice": {"currencyCode": "INR", "units": "200"},
                    "endPrice": {"currencyCode": "INR", "units": "400"},
                },
            },
            "park": {
                "id": "park", "_city": "bengaluru", "displayName": {"text": "Fixture Park"},
                "formattedAddress": "Bengaluru, India", "businessStatus": "OPERATIONAL",
                "primaryType": "park", "priceLevel": "PRICE_LEVEL_FREE",
            },
        },
        routes={
            ("cafe", "park", "walk"): Route(
                distance_metres=800, duration_minutes=12, mode="walk", source="Google Routes",
                verification_status="route_estimate",
            ),
        },
    )
    state.details.update(state.places)
    state.weather[("bengaluru", state.preferences.target_date, None)] = {"status": "unavailable"}
    return state


def make_proposal():
    return Proposal.model_validate({
        "title": "An easy afternoon", "explanation": "Two nearby stops at a relaxed pace.",
        "activities": [
            {
                "place_id": "cafe", "category": "food", "outdoors": False,
                "start_time": "14:00", "duration_minutes": 60,
                "description": "Have lunch.", "reason": "There are vegetarian options.",
            },
            {
                "place_id": "park", "category": "public_outdoors", "outdoors": True,
                "start_time": "15:20", "duration_minutes": 40,
                "description": "Take a walk.", "reason": "Fits your relaxed pace.",
            },
        ],
    })


def test_grounded_cost_and_time():
    state = make_state()
    plan, errors, _ = validate(state, make_proposal())
    assert not errors
    assert plan is not None
    assert plan.estimated_cost_min == 240
    assert plan.estimated_cost_max == 440
    assert plan.remaining_budget == 1560
    assert plan.duration_minutes == 130
    assert plan.buffer_minutes == 18
    assert plan.travel_minutes == 12
    assert plan.activities[0].maps_url.endswith("query_place_id=cafe")
    assert any("first stop" in item for item in plan.assumptions)
    assert any("Opening hours unavailable" in item for item in plan.activities[0].warnings)


def test_supported_transcript_scripts():
    assert supported_script("हैदराबाद में ₹1,000, maybe lunch and a museum.")
    assert supported_script("Hyderabad mein do hazaar rupaye.")
    assert not supported_script("꿈이라.")
    assert not supported_script("نگاه‌های برابر")


def test_budget_travel_and_dietary_hard_failures():
    state = make_state()
    state.preferences.budget = 300
    state.places["cafe"]["servesVegetarianFood"] = False
    proposal = make_proposal()
    proposal.activities[1].start_time = "15:05"
    plan, errors, _ = validate(state, proposal)
    assert plan is None
    assert any("required dietary" in error for error in errors)
    assert any("12 minutes for travel" in error for error in errors)
    assert any("exceeds the ₹300 budget" in error for error in errors)
    proposal.activities[1].place_id = "made-up-venue"
    assert any("retrieved place ID" in error for error in validate(state, proposal)[1])


def test_unspecified_times_are_scheduled_from_departure_and_routes():
    state = make_state()
    state.preferences.locality = "Indiranagar"
    state.preferences.start_time = "14:00"
    state.routes[("start:bengaluru:indiranagar", "cafe", "walk")] = Route(
        duration_minutes=7, mode="walk", source="Google Routes",
        verification_status="route_estimate",
    )
    proposal = make_proposal()
    for stop in proposal.activities:
        stop.start_time = None
    proposal.activities[1].buffer_before_minutes = 3

    plan, errors, _ = validate(state, proposal)
    assert not errors
    assert plan is not None
    assert [stop.start_time for stop in plan.activities] == ["14:07", "15:22"]
    assert plan.travel_minutes == 19
    assert plan.buffer_minutes == 13
    assert plan.duration_minutes == 132


def test_date_specific_hours_override_regular_hours():
    chosen = saturday()
    point = {"day": 6, "hour": 11, "minute": 0}
    end = {"day": 6, "hour": 18, "minute": 0}
    place = {
        "regularOpeningHours": {"periods": [{"open": point, "close": end}]},
        "currentOpeningHours": {"periods": []},
    }
    if (chosen - today_in_india()).days <= 6:
        assert opening_check(place, chosen, 840, 900) == (False, "date-specific")
    else:
        assert opening_check(place, chosen, 840, 900) == (True, "regular")
    assert opening_check(place, chosen + timedelta(days=7), 840, 900) == (True, "regular")


async def test_rpc_acknowledgement_controls_itinerary_and_revision():
    state = make_state()
    async with httpx.AsyncClient() as client:
        tools = {tool.name: tool for tool in make_tools(state, Providers(client, "fixture"))}
        payload = {"proposal": make_proposal().model_dump()}
        state.publish.side_effect = lambda topic, _: topic != "itinerary"
        result = await tools["display_itinerary"].ainvoke(payload)
        assert result["status"] == "unavailable"
        assert state.itinerary is None

        state.publish.side_effect = None
        assert (await tools["display_itinerary"].ainvoke(payload))["version"] == 1
        assert (await tools["confirm_itinerary"].ainvoke({}))["status"] == "ok"
        assert state.itinerary.status == "accepted"

        await tools["update_preferences"].ainvoke({"patch": {"budget": 350}, "request_revision": True})
        assert state.revision_needed
        assert state.preferences.available_minutes == 240
        assert (await tools["confirm_itinerary"].ainvoke({}))["status"] == "invalid"
        assert (await tools["display_itinerary"].ainvoke(payload))["status"] == "invalid"

        revised = make_proposal()
        revised.activities = [revised.activities[1]]
        result = await tools["display_itinerary"].ainvoke({"proposal": revised.model_dump()})
        assert result["version"] == 2
        assert state.itinerary.status == "draft"
        assert not state.revision_needed


async def test_display_checks_the_selected_stops_before_publishing():
    state = make_state()
    state.details.clear()
    state.routes.clear()
    state.weather.clear()
    expected = {"details", "route", "weather"}
    requested = set()

    async def lookup(request: httpx.Request):
        host = request.url.host
        if host == "places.googleapis.com":
            requested.add("details")
            return httpx.Response(200, json={"businessStatus": "OPERATIONAL"})
        if host == "routes.googleapis.com":
            requested.add("route")
            return httpx.Response(200, json={"routes": [{"distanceMeters": 800, "duration": "720s"}]})
        if host == "api.open-meteo.com":
            requested.add("weather")
            return httpx.Response(200, json={"hourly": {
                "time": [f"{saturday()}T15:00"], "temperature_2m": [25],
                "precipitation_probability": [5], "weather_code": [0],
            }})
        raise AssertionError(f"Unexpected lookup to {host}")

    state.places["park"]["location"] = {"latitude": 12.97, "longitude": 77.59}
    state.place_references.update({"venue_1": "cafe", "venue_2": "park"})
    proposal = make_proposal()
    proposal.activities[0].place_id = "venue_1"
    proposal.activities[1].place_id = "venue_2"
    async with httpx.AsyncClient(transport=httpx.MockTransport(lookup)) as client:
        display = next(tool for tool in make_tools(state, Providers(client, "fixture")) if tool.name == "display_itinerary")
        result = await display.ainvoke({"proposal": proposal.model_dump()})
    assert result["status"] == "ok"
    assert requested == expected
    assert [stop.place_id for stop in state.itinerary.activities] == ["cafe", "park"]
    assert state.itinerary.activities[0].travel_to_next.duration_minutes == 12


async def test_timeout_retries_once_and_generic_plan_has_no_venue_links():
    calls = 0

    async def timeout(request: httpx.Request):
        nonlocal calls
        calls += 1
        raise httpx.ReadTimeout("Fixture timeout", request=request)

    state = make_state()
    proposal = make_proposal()
    proposal.generic = True
    proposal.activities[0].generic_activity = "A vegetarian lunch"
    proposal.activities[1].generic_activity = "A neighbourhood park walk"
    for stop in proposal.activities:
        stop.place_id = None
    async with httpx.AsyncClient(transport=httpx.MockTransport(timeout)) as client:
        tools = {tool.name: tool for tool in make_tools(state, Providers(client, "fixture"))}
        args = {"proposal": proposal.model_dump()}
        assert (await tools["display_itinerary"].ainvoke(args))["status"] == "invalid"
        assert (await tools["search_places"].ainvoke({"query": "vegetarian lunch"}))["status"] == "unavailable"
        assert calls == 2
        assert (await tools["display_itinerary"].ainvoke(args))["status"] == "invalid"
        await tools["update_preferences"].ainvoke({"patch": {"allow_unverified_plan": True}})
        assert (await tools["display_itinerary"].ainvoke(args))["version"] == 1
    assert state.itinerary.verification_status == "unverified"
    assert all(stop.place_id is None and stop.maps_url is None for stop in state.itinerary.activities)
    assert any(event["status"] == "warning" for event in state.trace)


async def test_venue_outage_offers_recovery_in_hindi_without_inventing_a_plan():
    class UnusedModel:
        def bind_tools(self, _tools):
            return self

        async def ainvoke(self, _messages):
            raise AssertionError("The outage response should not require another model call.")

    async def timeout(request: httpx.Request):
        raise httpx.ReadTimeout("Fixture timeout", request=request)

    state = PlannerState(
        publish=AsyncMock(return_value=True), language="hi",
        preferences=Preferences(city="Hyderabad", budget=1000, available_minutes=180),
    )
    search = AIMessage(content="", tool_calls=[{
        "name": "search_places", "args": {"query": "historic sites"}, "id": "test-outage",
    }])
    async with httpx.AsyncClient(transport=httpx.MockTransport(timeout)) as client:
        graph = build_graph(state, Providers(client, "fixture"), UnusedModel())
        result = await graph.ainvoke({"messages": [HumanMessage(content="मुझे इतिहास पसंद है।"), search]})

    assert "क्या मैं फिर कोशिश करूँ" in result["messages"][-1].text
    assert state.itinerary is None
    assert not state.preferences.allow_unverified_plan
    assert any(step["tool"] == "search_places" and step["status"] == "warning" for step in state.trace)


def test_outdoor_stop_needs_weather_and_rejects_high_rain():
    state = make_state()
    state.weather.clear()
    proposal = make_proposal()
    assert any("Check the weather" in error for error in validate(state, proposal)[1])

    state.weather[("bengaluru", state.preferences.target_date, None)] = {
        "status": "ok", "hourly": [{
            "time": "15:00", "temperature_c": 27, "rain_probability": 85, "condition": "rain or showers",
        }],
    }
    assert any("High rain" in error for error in validate(state, proposal)[1])


async def test_graph_uses_tool_results_and_emits_only_spoken_text():
    class ScriptedModel:
        def bind_tools(self, tools):
            assert "display_itinerary" in {tool.name for tool in tools}
            return self

        async def ainvoke(self, messages):
            if isinstance(messages[-1], ToolMessage):
                assert "Bengaluru" in messages[-1].text
                return AIMessage(content="What budget would you like me to use?")
            return AIMessage(content="", tool_calls=[{
                "name": "update_preferences", "args": {"patch": {"city": "Bengaluru"}}, "id": "test-update",
            }])

    planner = PlannerState(publish=AsyncMock(return_value=True))
    async with httpx.AsyncClient() as client:
        graph = build_graph(planner, Providers(client, "fixture"), ScriptedModel())
        stream = graph.astream({"messages": [HumanMessage(content="I am in Bengaluru.")]}, stream_mode="custom")
        assert [part async for part in stream] == ["What budget would you like me to use?"]
    assert planner.preferences.city == "Bengaluru"
