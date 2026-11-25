from unittest.mock import AsyncMock

import httpx
import pytest

from providers import ProviderError, Providers
from schemas import Coordinates, Preferences, Proposal, today_in_india, upcoming_saturday
from state import PlannerState
from tools import make_tools


def ready_preferences(**updates):
    return Preferences(**{
        "city": "Bengaluru", "budget": 2000, "available_minutes": 120,
        "mood": "relaxed", "interests": ["cafes"], "constraints": [], **updates,
    })


def geo_feature(name="Fixture Café", identity="a" * 200, lat=12.97, lon=77.59, **extra):
    return {"type": "Feature", "properties": {
        "place_id": identity, "name": name, "lat": lat, "lon": lon,
        "formatted": f"{name}, Bengaluru, Karnataka", "country_code": "in",
        "categories": ["catering.cafe"], **extra,
    }}


def geo_response(request):
    if request.url.path == "/v1/geocode/search":
        assert request.url.params["filter"] == "countrycode:in"
        return httpx.Response(200, json={"features": [geo_feature()]})
    if request.url.path == "/v2/places":
        assert request.url.params["categories"] == "catering.cafe"
        assert "circle:77.59,12.97,3500" in request.url.params["filter"]
        return httpx.Response(200, json={"features": [geo_feature()]})
    raise AssertionError(f"Unexpected request to {request.url.host}{request.url.path}")


@pytest.mark.parametrize("google_status", [429, 403, 500, "network", None])
async def test_place_discovery_falls_back_to_geoapify(google_status):
    calls = []

    def handle(request):
        calls.append(request.url.host)
        if request.url.host == "places.googleapis.com":
            if google_status == "network":
                raise httpx.ConnectError("offline", request=request)
            return httpx.Response(google_status, json={})
        assert request.url.params["apiKey"] == "geo-test"
        return geo_response(request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        providers = Providers(client, "google-test" if google_status else "", "geo-test", "")
        place = (await providers.places_search("cafes", "Bengaluru", None, None, 5))[0]
        assert place["data_provider"] == "Geoapify Places"
        assert place["formattedAddress"].endswith("India")
        assert len(place["id"]) <= 110
        assert place["id"].startswith("geoapify:")
        assert not {"rating", "priceRange", "priceLevel", "regularOpeningHours", "businessStatus"} & place.keys()
        details = await providers.place_details(place["id"])
        assert details == place
        assert len(details["attributions"]) == 2
        assert "query_place_id" not in details["googleMapsUri"]
    assert calls.count("places.googleapis.com") == (1 if google_status else 0)


async def test_google_success_does_not_call_geoapify():
    def handle(request):
        assert request.url.host == "places.googleapis.com"
        return httpx.Response(200, json={"places": [{"id": "google-1", "displayName": {"text": "Cafe"}, "formattedAddress": "Bengaluru, India"}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        providers = Providers(client, "google-test", "geo-test", "")
        results = await providers.places_search("cafe", "Bengaluru", None, None, 5)
    assert results[0]["data_provider"] == "Google Places"


@pytest.mark.parametrize("mode", ["walk", "drive"])
async def test_geoapify_route_after_google_429_geocodes_starting_locality(mode):
    def handle(request):
        if request.url.host == "routes.googleapis.com":
            return httpx.Response(429, json={})
        if request.url.path == "/v1/geocode/search":
            assert request.url.params["text"] == "Indiranagar, Bengaluru, India"
            return geo_response(request)
        assert request.url.path == "/v1/routing"
        assert request.url.params["waypoints"] == "12.97,77.59|12.98,77.6"
        assert request.url.params["mode"] == mode
        return httpx.Response(200, json={"features": [{"properties": {"distance": 1250.4, "time": 601}}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        providers = Providers(client, "google-test", "geo-test", "")
        route = await providers.route({"address": "Indiranagar, Bengaluru, India"}, {"placeId": "google-1"}, mode,
                                      None, {"latitude": 12.98, "longitude": 77.6})
    assert route.verification_status == "route_estimate"
    assert route.distance_metres == 1251 and route.duration_minutes == 11
    assert "Geoapify" in route.source and "OpenStreetMap" in route.source


async def test_mixed_provider_route_sends_coordinates_to_google():
    import json

    def handle(request):
        assert request.url.host == "routes.googleapis.com"
        body = json.loads(request.content)
        assert body["origin"] == {"location": {"latLng": {"latitude": 12.97, "longitude": 77.59}}}
        assert body["destination"] == {"location": {"latLng": {"latitude": 12.98, "longitude": 77.6}}}
        assert "geoapify:" not in request.content.decode()
        return httpx.Response(200, json={"routes": [{"distanceMeters": 1200, "duration": "600s"}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        route = await Providers(client, "google-test", "geo-test", "").route(
            {"placeId": "geoapify:123"}, {"placeId": "google-1"}, "walk",
            {"latitude": 12.97, "longitude": 77.59}, {"latitude": 12.98, "longitude": 77.6})
    assert route.source == "Google Routes"


@pytest.mark.parametrize("routing_data", [{}, {"features": []}, {"features": [{"properties": {"distance": -1, "time": 10}}]}])
async def test_failed_routing_still_labels_approximation(routing_data):
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=routing_data))) as client:
        route = await Providers(client, "", "geo-test", "").route({}, {}, "walk",
            {"latitude": 12.97, "longitude": 77.59}, {"latitude": 12.98, "longitude": 77.6})
    assert route.verification_status == "approximate"
    assert route.source == "Approximation from straight-line distance"


@pytest.mark.parametrize("features", [[], [geo_feature(country_code="us")], [geo_feature(lat=999)]])
async def test_geocoding_rejects_empty_outside_india_or_invalid_coordinates(features):
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"features": features}))) as client:
        with pytest.raises(ProviderError, match="could not locate"):
            await Providers(client, "", "geo-test", "").geocode("Bengaluru")


@pytest.mark.parametrize("tavily_status", [200, 429])
async def test_tavily_preserves_unknown_fields_and_osm_hours(tavily_status):
    def handle(request):
        if request.url.host == "api.tavily.com":
            assert request.headers["authorization"] == "Bearer tavily-test"
            return httpx.Response(tavily_status, json={"results": [
                {"url": "https://venue.example/menu", "title": "Fixture Café", "content": "Open 24 hours, rated 5 stars, meals ₹100."},
                {"url": "javascript:alert(1)", "content": "bad"},
            ]})
        return httpx.Response(200, json={"features": [geo_feature(datasource={"raw": {"opening_hours": "Mo-Sa 09:00-18:00"}})]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        providers = Providers(client, "", "geo-test", "tavily-test")
        place = (await providers.places_search("cafe", "Bengaluru", None, Coordinates(latitude=12.97, longitude=77.59), 5))[0]
        details = await providers.place_details(place["id"])
    assert details["opening_hours_text"] == "Mo-Sa 09:00-18:00"
    assert not {"rating", "priceRange", "regularOpeningHours"} & details.keys()
    if tavily_status == 200:
        assert len(details["venue_information"]) == 1
        assert details["venue_information"][0]["verification_status"] == "unverified_web_context"
    else:
        assert "supplement_warning" in details


@pytest.mark.parametrize("tavily_status", [200, 429])
async def test_tools_display_grounded_geoapify_itinerary_with_credits_and_real_trace(tavily_status):
    def handle(request):
        if request.url.host == "api.tavily.com":
            return httpx.Response(tavily_status, json={"results": [{"url": "https://venue.example/menu", "content": "Venue menu"}]})
        return geo_response(request)

    state = PlannerState(publish=AsyncMock(return_value=True), preferences=ready_preferences(
        city="Bengaluru", budget=2000, available_minutes=120,
    ))
    # Use a future Saturday even when the suite runs on Saturday evening.
    from datetime import timedelta
    state.preferences.target_date = upcoming_saturday(today_in_india() + timedelta(days=1))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        tools = {t.name: t for t in make_tools(state, Providers(client, "", "geo-test", "tavily-test"))}
        result = await tools["search_places"].ainvoke({"query": "cafe"})
        assert result["status"] == "ok"
        reference = result["places"][0]["reference"]
        proposal = Proposal(title="Cafe afternoon", explanation="A nearby cafe.", activities=[{
            "place_id": reference, "category": "food", "outdoors": False,
            "duration_minutes": 60, "description": "Have coffee.", "reason": "Fits the afternoon.",
        }])
        outcome = await tools["display_itinerary"].ainvoke({"proposal": proposal.model_dump()})
    assert outcome["status"] == "ok"
    plan = state.itinerary
    assert plan.verification_status == "grounded" and plan.maps_fallback is None
    activity = plan.activities[0]
    assert activity.rating is None and activity.rating_count is None
    assert activity.cost_source == "Unverified planning estimate"
    assert any("Geoapify" in credit for credit in activity.attributions)
    assert any("OpenStreetMap" in credit for credit in activity.attributions)
    if tavily_status == 200:
        assert any("https://venue.example/menu" in warning for warning in activity.warnings)
    assert any("Geoapify Places" in event["description"] for event in state.trace)
    assert any("Tavily" in event["description"] for event in state.trace)
    assert not any("maps_failure" in event for event in state.trace)


@pytest.mark.parametrize("same_branch", [True, False])
async def test_google_details_failure_matches_geoapify_conservatively(same_branch):
    def handle(request):
        if request.url.path.endswith("places:searchText"):
            return httpx.Response(200, json={"places": [{
                "id": "google-1", "displayName": {"text": "Fixture Café"},
                "formattedAddress": "Bengaluru, India", "location": {"latitude": 12.97, "longitude": 77.59},
                "rating": 4.2,
            }]})
        if request.url.host == "places.googleapis.com":
            return httpx.Response(429, json={})
        return httpx.Response(200, json={"features": [geo_feature(
            lat=12.97 if same_branch else 13.5,
            datasource={"raw": {"opening_hours": "Mo-Sa 09:00-18:00"}},
        )]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        providers = Providers(client, "google-test", "geo-test", "")
        await providers.places_search("cafe", "Bengaluru", None, None, 5)
        details = await providers.place_details("google-1")
    assert details["id"] == "google-1" and details["rating"] == 4.2
    if same_branch:
        assert details["data_provider"] == "Google Places + Geoapify Places"
        assert details["opening_hours_text"] == "Mo-Sa 09:00-18:00"
        assert any("OpenStreetMap" in credit["provider"] for credit in details["attributions"])
    else:
        assert details["data_provider"] == "Google Places"
        assert "opening_hours_text" not in details
        assert "detail_warning" in details


async def test_all_place_providers_failed_still_triggers_existing_maps_failure():
    state = PlannerState(publish=AsyncMock(return_value=True), preferences=ready_preferences(city="Bengaluru"))
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(429, json={}))) as client:
        tools = {t.name: t for t in make_tools(state, Providers(client, "google-test", "geo-test", ""))}
        result = await tools["search_places"].ainvoke({"query": "cafe"})
    assert result["status"] == "unavailable" and result["maps_failure"] == "places"
    assert state.last_places_failure
    assert "geo-test" not in result["error"]


async def test_geoapify_env_key_and_geocoding_cache(monkeypatch):
    monkeypatch.setenv("GEOAPIFY_API_KEY", "env-geo-test")
    count = 0

    def handle(request):
        nonlocal count
        count += 1
        assert request.url.params["apiKey"] == "env-geo-test"
        return geo_response(request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        providers = Providers(client, "", tavily_key="")
        assert await providers.geocode("Bengaluru") == await providers.geocode("Bengaluru")
    assert count == 1
    assert "Geoapify" in providers.geocoding_sources["Bengaluru"]


async def test_successful_empty_search_does_not_enable_sample_data():
    state = PlannerState(publish=AsyncMock(return_value=True), preferences=ready_preferences(city="Bengaluru"))
    state.last_places_failure = "Earlier temporary outage"

    def handle(request):
        if request.url.path == "/v1/geocode/search":
            return geo_response(request)
        return httpx.Response(200, json={"features": []})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        tools = {t.name: t for t in make_tools(state, Providers(client, "", "geo-test", ""))}
        result = await tools["search_places"].ainvoke({"query": "cafe"})
    assert result["status"] == "empty" and "maps_failure" not in result
    assert state.last_places_failure is None


async def test_parallel_partial_failure_keeps_real_venues():
    from langchain_core.messages import AIMessage
    from graph import build_graph

    class AvailableVenuesModel:
        def bind_tools(self, _):
            return self

        async def ainvoke(self, messages):
            return AIMessage(content="I found a real cafe; the park lookup failed.")

    def handle(request):
        if request.url.path == "/v1/geocode/search":
            return geo_response(request)
        if request.url.params["categories"] == "leisure.park":
            return httpx.Response(429, json={})
        return geo_response(request)

    state = PlannerState(publish=AsyncMock(return_value=True), preferences=ready_preferences(
        city="Bengaluru", budget=2000, available_minutes=120,
    ))
    calls = AIMessage(content="", tool_calls=[
        {"name": "search_places", "args": {"query": query}, "id": f"call-{query}"}
        for query in ("cafe", "park")
    ])
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        graph = build_graph(state, Providers(client, "", "geo-test", ""), AvailableVenuesModel())
        await graph.ainvoke({"messages": [calls]})
    assert len(state.places) == 1
    assert state.itinerary is None
    assert not any(step["tool"] == "maps_fallback" for step in state.trace)


@pytest.mark.parametrize("payload", [{}, {"features": None}, {"features": [None]}, {"features": [{"properties": None}]}])
async def test_malformed_geoapify_data_is_a_provider_failure(payload):
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload))) as client:
        providers = Providers(client, "", "geo-test", "")
        with pytest.raises(ProviderError, match="Geoapify Places"):
            await providers.places_search("cafe", "Bengaluru", None, Coordinates(latitude=12.97, longitude=77.59), 5)


async def test_geoapify_outage_automatically_displays_labelled_sample():
    from datetime import timedelta
    from langchain_core.messages import AIMessage
    from graph import build_graph

    class UnusedModel:
        def bind_tools(self, _):
            return self

        async def ainvoke(self, _):
            raise AssertionError("Provider failure should display the sample without a model request.")

    state = PlannerState(publish=AsyncMock(return_value=True), preferences=ready_preferences(
        city="Bengaluru", budget=2000, available_minutes=120,
        target_date=upcoming_saturday(today_in_india() + timedelta(days=1)),
    ))
    calls = AIMessage(content="", tool_calls=[{
        "name": "search_places", "args": {"query": "cafe"}, "id": "outage",
    }])
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(429, json={}))) as client:
        graph = build_graph(state, Providers(client, "", "geo-test", "tavily-test"), UnusedModel())
        await graph.ainvoke({"messages": [calls]})
    assert state.itinerary.maps_fallback == "places"
    assert state.itinerary.verification_status == "unverified"
    assert all(stop.place_id is None and stop.rating is None for stop in state.itinerary.activities)
    assert all(stop.maps_url is None for stop in state.itinerary.activities)
