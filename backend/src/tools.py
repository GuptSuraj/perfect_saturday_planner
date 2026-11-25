import asyncio
import logging
from collections.abc import Callable
from datetime import date
from functools import wraps
from time import perf_counter
from typing import Annotated

from langchain_core.tools import tool
from pydantic import Field

from providers import ProviderError, Providers
from schemas import Coordinates, Mode, Preferences, Proposal, ShortText, today_in_india
from state import PlannerState
from validation import plan_date, starting_origin, validate

logger = logging.getLogger(__name__)


def make_tools(state: PlannerState, providers: Providers):
    def traced(label: str, *, serialized: bool = False):
        def decorate(function: Callable):
            @wraps(function)
            async def wrapped(*args, **kwargs):
                started = perf_counter()
                name = function.__name__
                trace_id = f"{len(state.trace)}-{int(started * 1000)}-{name}"

                async def emit(status: str, description: str):
                    event = {"id": trace_id, "tool": name, "status": status, "description": description}
                    if status != "running":
                        event["duration_ms"] = round((perf_counter() - started) * 1000)
                    state.trace.append(event)
                    await state.publish("trace", event)

                state.calls += 1
                if state.calls > 90:
                    return {"status": "unavailable", "error": "Session tool limit reached. Start a new conversation."}
                await emit("running", label)
                try:
                    async with asyncio.timeout(16):
                        if serialized:
                            async with state.writes:
                                result = await function(*args, **kwargs)
                        else:
                            result = await function(*args, **kwargs)
                except asyncio.CancelledError:
                    await emit("warning", "Tool execution interrupted.")
                    raise
                except ProviderError as error:
                    result = {"status": "unavailable", "error": str(error)}
                except TimeoutError:
                    result = {"status": "unavailable", "error": f"{label} timed out."}
                except Exception:
                    logger.exception("Planner tool failed: %s", name)
                    result = {"status": "unavailable", "error": f"{label} failed unexpectedly."}
                status = "success" if result["status"] == "ok" else "warning"
                await emit(status, result.get("summary") or result.get("error") or label)
                return result

            return tool(wrapped)
        return decorate

    @traced("Updating your preferences", serialized=True)
    async def update_preferences(patch: Preferences, request_revision: bool = False) -> dict:
        """Save newly stated or corrected preferences. Send only the fields that changed.
        An explicit null clears a preference. Send complete replacement lists for interests
        and constraints. Set request_revision for a request to change the displayed plan
        even when preferences stay the same. Call alone before any city-dependent tools.
        """
        updates = patch.model_dump(exclude_unset=True)
        if patch.target_date and (patch.target_date.weekday() != 5 or patch.target_date < today_in_india()):
            return {"status": "invalid", "error": "target_date must be a Saturday on or after today in India."}
        previous = state.preferences
        preferences = Preferences.model_validate({**previous.model_dump(), **updates})
        if preferences.city != previous.city:
            state.last_places_failure = None
            if "locality" not in updates:
                preferences.locality = None
        changed = previous != preferences
        state.preferences = preferences
        if state.itinerary and (changed or request_revision):
            state.revision_needed = True
        data = {
            "preferences": preferences.model_dump(mode="json", exclude_none=True),
            "revision_needed": state.revision_needed,
        }
        await state.publish("preferences", data)
        return {"status": "ok", **data, "summary": "Preferences saved" if changed else "Preferences reviewed"}

    @traced("Searching Google Places")
    async def search_places(
        query: ShortText,
        locality: ShortText | None = None,
        location_bias: Coordinates | None = None,
        max_results: Annotated[int, Field(ge=1, le=5)] = 5,
    ) -> dict:
        """Search for real venues using an activity-specific query. The saved Indian city
        is automatically appended. Optionally choose a locality (defaults to the saved
        one) or bias results toward known coordinates. Returns up to five venue records.
        """
        city, city_key = state.preferences.city, state.city_key
        if not city:
            return {"status": "invalid", "error": "Save the user's city before searching Google Places."}
        locality = locality or state.preferences.locality
        bias_key = (location_bias.latitude, location_bias.longitude) if location_bias else None
        cache_key = (city_key, (locality or "").casefold(), query.casefold(), bias_key, max_results)
        if cache_key in state.searches:
            places = [state.places[place_id] for place_id in state.searches[cache_key]]
        else:
            try:
                places = await providers.places_search(query, city, locality, location_bias, max_results)
            except ProviderError as error:
                if city_key == state.city_key:
                    state.last_places_failure = str(error)
                raise
            places = [place for place in places if place["formattedAddress"].rstrip().endswith("India")]
            for place in places:
                state.places[place["id"]] = {**place, **state.places.get(place["id"], {}), "_city": city_key}
            state.searches[cache_key] = [place["id"] for place in places]
        if not places:
            if city_key == state.city_key:
                state.last_places_failure = "No matching venues"
            return {"status": "empty", "places": [], "summary": "Google Places found no matching venues"}
        return {
            "status": "ok", "places": [{key: val for key, val in place.items() if not key.startswith("_")} for place in places],
            "summary": f"Retrieved {len(places)} Google Places results",
        }

    @traced("Checking venue details")
    async def get_place_details(place_id: ShortText) -> dict:
        """Look up hours, vegetarian options, accessibility, website, price and other
        details for a retrieved Google place ID. Live music availability is a venue
        feature and does not verify a performance on the selected Saturday.
        """
        place = state.places.get(place_id)
        if not place or place.get("_city") != state.city_key:
            return {"status": "invalid", "error": "Use a place ID already retrieved for the current city."}
        if place_id not in state.details:
            details = await providers.place_details(place_id)
            place = {**place, **details}
            state.places[place_id] = place
            state.details.add(place_id)
        name = place["displayName"]["text"]
        return {"status": "ok", "place": {k: v for k, v in place.items() if not k.startswith("_")}, "summary": f"Details checked for {name}"}

    @traced("Checking the Saturday forecast")
    async def get_weather(place_id: ShortText | None = None, forecast_date: date | None = None) -> dict:
        """Retrieve hourly Open-Meteo temperature, conditions and rain probability.
        Use a retrieved venue ID to get a local forecast or omit it to geocode the city.
        Omit forecast_date to use the planned Saturday.
        """
        city, city_key = state.preferences.city, state.city_key
        selected_date = forecast_date or plan_date(state)
        if not city:
            return {"status": "invalid", "error": "Save the city before checking weather."}
        key = (city_key, selected_date, place_id)
        if key not in state.weather:
            place = state.places.get(place_id or "")
            if place_id and (not place or place.get("_city") != city_key):
                return {"status": "invalid", "error": "Use a known venue in the current city or omit the place ID."}
            coords = place.get("location") if place else None
            try:
                coordinates = Coordinates.model_validate(coords) if coords else await providers.geocode(city)
                state.weather[key] = await providers.forecast(coordinates, selected_date)
            except ProviderError as error:
                state.weather[key] = {"status": "unavailable", "error": str(error)}
        forecast = state.weather[key]
        if forecast["status"] != "ok":
            return forecast
        return {**forecast, "summary": f"Forecast retrieved for {city} from Open-Meteo"}

    @traced("Calculating travel time")
    async def calculate_route(
        destination_place_id: ShortText,
        origin_place_id: ShortText | None = None,
        mode: Mode | None = None,
    ) -> dict:
        """Find travel time to a retrieved place. Pass an origin place ID for a venue-to-venue
        leg, or omit it to start from the saved locality. Uses the saved transport mode
        (walking if absent) unless overridden. Can return a labelled approximation.
        """
        destination = state.places.get(destination_place_id)
        city_key = state.city_key
        if not destination or destination.get("_city") != city_key:
            return {"status": "invalid", "error": "Use a retrieved destination place ID in the current city."}
        if origin_place_id == destination_place_id:
            return {"status": "invalid", "error": "Use two distinct places."}
        mode = mode or state.preferences.transport or "walk"
        if origin_place_id:
            origin = state.places.get(origin_place_id)
            if not origin or origin.get("_city") != city_key:
                return {"status": "invalid", "error": "Use a retrieved origin place ID in the current city."}
            from_waypoint = {"placeId": origin_place_id}
        elif state.preferences.locality:
            origin = {}
            from_waypoint = {"address": f"{state.preferences.locality}, {state.preferences.city}, India"}
        else:
            return {"status": "invalid", "error": "No starting locality. Exclude travel to the first stop."}
        key = (origin_place_id or starting_origin(state), destination_place_id, mode)
        if key not in state.routes:
            state.routes[key] = await providers.route(
                from_waypoint, {"placeId": destination_place_id}, mode,
                origin.get("location"), destination.get("location"),
            )
        route = state.routes[key]
        status = "ok" if route.verification_status == "route_estimate" else route.verification_status
        return {
            **route.model_dump(), "status": status,
            "summary": f"{route.duration_minutes} min via {route.source}" if route.duration_minutes else "Route estimate unavailable",
        }

    @traced("Estimating costs")
    async def estimate_cost(proposal: Proposal) -> dict:
        """Compute the proposal's activity, transportation and contingency costs for the
        full group. Prices and totals come from stored venue data or conservative estimates.
        """
        itinerary, errors, totals = validate(state, proposal)
        if not totals:
            return {"status": "invalid", "error": errors[0]}
        summary = f"Estimated ₹{totals['estimated_cost_min']}–₹{totals['estimated_cost_max']}"
        return {
            "status": "ok" if itinerary else "warning", "summary": summary,
            "costs": totals, "validation_errors": errors,
        }

    @traced("Validating the itinerary")
    async def validate_itinerary(proposal: Proposal) -> dict:
        """Check grounding, operating and opening hours, hard constraints, weather,
        overlap, travel, budget, and time. Use returned errors to adjust the proposal.
        """
        itinerary, errors, totals = validate(state, proposal)
        return {
            "status": "ok" if itinerary else "invalid", "errors": errors, "totals": totals,
            "warnings": itinerary.warnings if itinerary else totals.get("warnings", []),
            "summary": "Itinerary validated" if itinerary else errors[0],
        }

    @traced("Sending the itinerary to your screen", serialized=True)
    async def display_itinerary(proposal: Proposal) -> dict:
        """Revalidate a complete itinerary proposal, send it to the browser, and only
        commit its new version if the browser acknowledges it. Call for every plan
        and revision. Only say that it is visible after a successful result.
        Leave individual start times blank to schedule from the user's departure time
        and the stored route legs. Supply start times only for intentionally fixed stops.
        """
        itinerary, errors, _ = validate(state, proposal)
        if not itinerary:
            return {"status": "invalid", "errors": errors, "summary": errors[0]}
        if not await state.publish("itinerary", itinerary.model_dump(mode="json")):
            return {"status": "unavailable", "error": "The browser did not acknowledge the itinerary."}
        state.itinerary = itinerary
        state.revision_needed = False
        return {"status": "ok", "version": itinerary.version, "summary": f"Itinerary version {itinerary.version} displayed"}

    @traced("Recording your approval", serialized=True)
    async def confirm_itinerary() -> dict:
        """Mark the displayed itinerary accepted only after the user explicitly approves it."""
        if state.itinerary is None or state.revision_needed:
            return {"status": "invalid", "error": "Display an up-to-date itinerary before accepting approval."}
        if state.itinerary.status == "accepted":
            return {"status": "ok", "summary": "Itinerary already confirmed"}
        confirmed = state.itinerary.model_copy(update={"status": "accepted"})
        if not await state.publish("itinerary", confirmed.model_dump(mode="json")):
            return {"status": "unavailable", "error": "The browser did not acknowledge the confirmation."}
        state.itinerary = confirmed
        return {"status": "ok", "summary": "Itinerary confirmed"}

    return [
        update_preferences, search_places, get_place_details, get_weather, calculate_route,
        estimate_cost, validate_itinerary, display_itinerary, confirm_itinerary,
    ]
