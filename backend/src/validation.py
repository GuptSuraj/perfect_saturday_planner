import math
from datetime import date, datetime, timedelta

from language import romanize_caption
from schemas import (
    INDIA, Activity, Category, Itinerary, MapsFailure, Mode, Proposal, Route, Transit, clock,
    minutes, today_in_india, upcoming_saturday,
)
from state import PlannerState

FOOD_TYPES = {"restaurant", "cafe", "coffee_shop", "bakery", "bar", "food", "meal_takeaway", "ice_cream_shop"}
OUTDOOR_TYPES = {"park", "garden", "botanical_garden", "hiking_area", "national_park", "playground", "beach"}
PRICE_RANGES = {
    "PRICE_LEVEL_FREE": (0, 0),
    "PRICE_LEVEL_INEXPENSIVE": (150, 350),
    "PRICE_LEVEL_MODERATE": (300, 700),
    "PRICE_LEVEL_EXPENSIVE": (650, 1300),
    "PRICE_LEVEL_VERY_EXPENSIVE": (1100, 2500),
}
CATEGORY_RANGES = {
    "food": (200, 500), "public_outdoors": (0, 100), "browse": (0, 100),
    "ticketed": (200, 650), "other": (100, 350),
}


def plan_date(state: PlannerState) -> date:
    return state.preferences.target_date or upcoming_saturday()


def place_category(place: dict, proposed: Category) -> Category:
    types = set(place.get("types", []))
    if types & FOOD_TYPES or any(value.endswith("_restaurant") for value in types):
        return "food"
    return proposed


def activity_cost(place: dict, category: Category, people: int) -> tuple[int, int, str]:
    price = place.get("priceRange") or {}
    start, end = price.get("startPrice") or {}, price.get("endPrice") or {}
    if start.get("currencyCode") == end.get("currencyCode") == "INR":
        def amount(money: dict) -> float:
            return int(money.get("units", 0)) + money.get("nanos", 0) / 1_000_000_000

        low, high = math.floor(amount(start)), math.ceil(amount(end))
        if 0 <= low <= high:
            return low * people, high * people, "Google Places price range estimate"

    level = PRICE_RANGES.get(place.get("priceLevel"))
    if level is not None:
        low, high = level
        return low * people, high * people, "Estimate from Google Places price level"

    low, high = CATEGORY_RANGES[category]
    return low * people, high * people, "Unverified planning estimate"


def ride_cost(routes: list[Route], mode: Mode, people: int) -> tuple[int, int]:
    if mode == "walk":
        return 0, 0
    vehicles = math.ceil(people / 4)
    distances = [(route.distance_metres if route.distance_metres is not None else 6000) / 1000 for route in routes]
    return (
        sum(math.ceil(40 + 13 * distance) for distance in distances) * vehicles,
        sum(math.ceil(90 + 25 * distance) for distance in distances) * vehicles,
    )


def generic_leg(mode: Mode) -> Route:
    return Route(
        duration_minutes=15 if mode == "walk" else 25, mode=mode,
        source="Planning allowance; route unavailable", verification_status="approximate",
    )


def starting_origin(state: PlannerState) -> str:
    return f"start:{state.locality_key[0]}:{state.locality_key[1]}"


def local_day(point: dict) -> date | None:
    fields = point.get("date")
    if fields and all(fields.get(key) for key in ("year", "month", "day")):
        return date(fields["year"], fields["month"], fields["day"])
    return None


def opening_check(place: dict, day: date, start: int, end: int) -> tuple[bool | None, str]:
    today = today_in_india()
    current = place.get("currentOpeningHours") or {}
    regular = place.get("regularOpeningHours") or {}
    has_current_hours = today <= day <= today + timedelta(days=6) and "periods" in current
    hours = current if has_current_hours else regular
    label = "date-specific" if has_current_hours else "regular"
    periods = hours.get("periods")
    if periods is None:
        return None, label
    day_index = (day.weekday() + 1) % 7
    for period in periods:
        opens, closes = period.get("open") or {}, period.get("close")
        if not opens:
            continue
        if closes is None and opens.get("day") == 0 and not opens.get("hour") and not opens.get("minute"):
            return True, label
        if not closes:
            continue
        opening_date, closing_date = local_day(opens), local_day(closes)
        open_offset = (
            (opening_date - day).days if has_current_hours and opening_date
            else -((day_index - opens.get("day", 0)) % 7)
        )
        open_minute = open_offset * 1440 + opens.get("hour", 0) * 60 + opens.get("minute", 0)
        if has_current_hours and closing_date:
            close_offset = (closing_date - day).days
        else:
            close_offset = open_offset + ((closes.get("day", 0) - opens.get("day", 0)) % 7)
        close_minute = close_offset * 1440 + closes.get("hour", 0) * 60 + closes.get("minute", 0)
        if open_minute <= start and end <= close_minute:
            return True, label
    return False, label


def forecast_for(state: PlannerState, place_id: str | None, day: date) -> dict:
    key = (state.city_key, day)
    forecasts = [
        state.weather.get((*key, place_id), {}),
        state.weather.get((*key, None), {}),
        *(value for cache_key, value in state.weather.items() if cache_key[:2] == key),
    ]
    return next((record for record in forecasts if record.get("status") == "ok"),
                next((record for record in forecasts if record), {}))


def hourly_window(forecast: dict, start: int, end: int) -> list[dict]:
    return [
        row for row in forecast.get("hourly", [])
        if minutes(row["time"]) < end and minutes(row["time"]) + 60 > start
    ]


def validate(
    state: PlannerState, proposal: Proposal, *, maps_fallback: MapsFailure | None = None,
) -> tuple[Itinerary | None, list[str], dict]:
    prefs = state.preferences
    if prefs.city is None or prefs.budget is None or prefs.available_minutes is None:
        return None, ["Collect the city, total INR budget and available time first."], {}

    day = plan_date(state)
    errors: list[str] = []
    warnings: list[str] = []
    assumptions: list[str] = []
    activities: list[Activity] = []
    weather_rows: list[dict] = []
    routes: list[Route] = []
    starting_leg: Transit | None = None
    mode = prefs.transport or "walk"
    people = prefs.people or 1
    if prefs.people is None:
        assumptions.append("Costs assume one person.")
    if prefs.transport is None:
        assumptions.append("Walking between stops.")
    if not prefs.locality:
        assumptions.append("Travel to the first stop is excluded because a starting locality was not provided.")
    assumptions.append("Prices are estimates in INR for the whole group. Return travel is excluded.")

    if proposal.generic:
        if maps_fallback is None and not state.last_places_failure:
            errors.append("Generic plans require an unavailable or empty real-place search.")
        if maps_fallback is None and not prefs.allow_unverified_plan:
            errors.append("Offer an unverified generic plan and wait for the user's agreement.")
        warnings.append("Confirm venue access, hours, prices, and directions before leaving.")

    if day.weekday() != 5 or day < today_in_india():
        errors.append("Choose today if it is Saturday or a future Saturday in India.")

    first = proposal.activities[0]
    if prefs.locality:
        route = state.routes.get((starting_origin(state), first.place_id, mode))
        if proposal.generic:
            route = generic_leg(mode)
        if route is None:
            errors.append(f"Calculate a {mode} route from the starting locality to {first.place_id}.")
        else:
            if route.duration_minutes is None:
                route = generic_leg(mode)
            routes.append(route)
            name = state.places.get(first.place_id or "", {}).get("displayName", {}).get("text")
            starting_leg = Transit(**route.model_dump(), destination=name or first.generic_activity or "First activity")
            if route.verification_status == "approximate":
                warnings.append("Travel from the starting locality is approximate. Allow extra time.")
            else:
                assumptions.append(f"First route starts from the {prefs.locality} locality, source: {route.source}.")

    first_travel = (starting_leg.duration_minutes or 0) if starting_leg else 0
    departure = prefs.start_time or proposal.departure_time
    if departure:
        window_start = minutes(departure)
    elif first.start_time:
        window_start = minutes(first.start_time) - first_travel - first.buffer_before_minutes
    else:
        window_start = minutes("10:00" if prefs.available_minutes >= 360 else "15:00")
    if prefs.start_time is None:
        assumptions.append(f"Suggested start time is {clock(max(0, window_start))} in {prefs.city}.")
    transfers = [
        generic_leg(mode) if proposal.generic else state.routes.get((origin.place_id, destination.place_id, mode))
        for origin, destination in zip(proposal.activities, proposal.activities[1:])
    ]
    starts: list[int] = []
    ends: list[int] = []
    for index, stop in enumerate(proposal.activities):
        previous_leg = transfers[index - 1] if index else starting_leg
        travel_time = (previous_leg.duration_minutes or 0) if previous_leg else 0
        earliest = (ends[-1] if ends else window_start) + travel_time + stop.buffer_before_minutes
        start = minutes(stop.start_time) if stop.start_time else earliest
        starts.append(start)
        ends.append(start + stop.duration_minutes)

    if starts[0] - first_travel - first.buffer_before_minutes < window_start or window_start < 0:
        errors.append("Allow time to reach the first activity after the requested start time.")
    now = datetime.now(INDIA)
    if day == now.date() and window_start < now.hour * 60 + now.minute:
        errors.append("The plan starts in the past. Move it later today or use the next Saturday.")
    if max(ends) + proposal.end_buffer_minutes >= 1440:
        errors.append("Keep the plan within the selected Saturday.")
    duration = ends[-1] + proposal.end_buffer_minutes - window_start
    if duration > prefs.available_minutes:
        errors.append(f"Total duration is {duration} minutes and the user has {prefs.available_minutes} minutes.")

    seen: set[str] = set()
    for index, stop in enumerate(proposal.activities):
        start, end = starts[index], ends[index]
        stop_warnings: list[str] = []
        place = state.places.get(stop.place_id or "", {})
        name = place.get("displayName", {}).get("text") or stop.generic_activity or f"Stop {index + 1}"
        if proposal.generic:
            if stop.place_id or not stop.generic_activity:
                errors.append("Generic plans must use only unnamed activity descriptions.")
            if prefs.accessibility_required:
                stop_warnings.append("Confirm step-free entry, facilities, and the route before heading out.")
        elif not stop.place_id or not place or place.get("_city") != state.city_key or stop.generic_activity:
            errors.append(f"Stop {index + 1} needs a retrieved place ID in {prefs.city}.")
        elif stop.place_id in seen:
            errors.append(f"{name} appears more than once.")
        if stop.place_id:
            seen.add(stop.place_id)
        if place:
            business_status = place.get("businessStatus")
            if business_status and business_status != "OPERATIONAL":
                errors.append(f"{name} is not listed as operational.")
            if not business_status:
                stop_warnings.append("Operational status unavailable.")
            if place.get("opening_hours_text"):
                stop_warnings.append(f"Listed hours ({place.get('data_provider', 'venue provider')}): {place['opening_hours_text']}. Confirm applicability before visiting.")
            if place.get("detail_warning"):
                stop_warnings.append(place["detail_warning"])
            for evidence in place.get("venue_information", []):
                stop_warnings.append(f"Additional venue information via Tavily (unverified; check venue and branch): {evidence['url']}")
            opening, source = opening_check(place, day, start, end)
            if opening is False:
                errors.append(f"{name} is closed during that visit according to {source} opening hours.")
            if opening is None:
                stop_warnings.append("Opening hours unavailable. Verify before visiting.")
            if opening and source == "regular":
                stop_warnings.append("Based on regular hours. Holiday changes are possible.")
            if prefs.accessibility_required and not place.get("accessibilityOptions", {}).get("wheelchairAccessibleEntrance"):
                errors.append(f"Wheelchair-accessible entrance is not verified for {name}.")

        category = place_category(place, stop.category)
        if category == "food" and prefs.dietary:
            if proposal.generic:
                stop_warnings.append("Confirm your dietary requirements directly before choosing a venue.")
            elif prefs.dietary == "vegetarian_options" and place.get("servesVegetarianFood") is True:
                stop_warnings.append(f"{place.get('data_provider', 'Google Places')} lists vegetarian options. Confirm menu and preparation details.")
            else:
                errors.append(f"The required dietary options are not verified for {name}.")

        outdoors = stop.outdoors or place.get("primaryType") in OUTDOOR_TYPES
        if (prefs.environment == "indoors" and outdoors) or (prefs.environment == "outdoors" and not outdoors):
            errors.append(f"{name} conflicts with the requested {prefs.environment} setting.")
        forecast = forecast_for(state, stop.place_id, day)
        rows = hourly_window(forecast, start, end)
        weather_rows.extend(rows)
        if outdoors:
            if not forecast and not proposal.generic:
                errors.append("Check the weather for this date before scheduling an outdoor stop.")
            elif not rows:
                stop_warnings.append("Outdoor conditions are unverified. Check the local forecast before leaving.")
            elif any(row["condition"] == "thunderstorms" or row["rain_probability"] >= 70 for row in rows):
                errors.append(f"High rain or thunderstorm risk during the outdoor stop at {name}.")
            elif any(row["rain_probability"] >= 45 or row["condition"] == "rain or showers" for row in rows):
                stop_warnings.append("Rain is possible during this stop. Check conditions before leaving.")

        if any("crowd" in constraint.casefold() for constraint in prefs.constraints):
            stop_warnings.append("Crowd levels are unverified.")

        travel = None
        if index + 1 < len(proposal.activities):
            next_stop = proposal.activities[index + 1]
            next_place = state.places.get(next_stop.place_id or "", {})
            next_name = next_place.get("displayName", {}).get("text") or next_stop.generic_activity or "Next stop"
            route = transfers[index]
            if route is None or route.duration_minutes is None:
                errors.append(f"Get a usable {mode} route from {stop.place_id} to {next_stop.place_id}.")
            else:
                routes.append(route)
                travel = Transit(**route.model_dump(), destination=next_name)
                if route.duration_minutes > min(45, prefs.available_minutes * 0.35):
                    errors.append(f"The {route.duration_minutes}-minute route from {name} to {next_name} is too long.")
                if route.verification_status == "approximate":
                    stop_warnings.append("Travel to the next stop is approximate.")
            gap = starts[index + 1] - end
            travel_needed = (route.duration_minutes or 0) if route else 0
            required = travel_needed + next_stop.buffer_before_minutes
            if gap < 0:
                errors.append(f"{name} overlaps {next_name}.")
            elif gap < required:
                errors.append(f"Allow at least {required} minutes for travel and buffer after {name}.")

        low, high, cost_source = (0, 0, "Free-option allowance") if maps_fallback and category == "browse" else activity_cost(place, category, people)
        if cost_source == "Unverified planning estimate":
            stop_warnings.append("Price is unverified. Check venue prices before visiting.")
        activities.append(Activity(
            place_id=stop.place_id, name=name, address=place.get("formattedAddress"),
            start_time=clock(start), end_time=clock(end), duration_minutes=stop.duration_minutes,
            description=romanize_caption(stop.description), reason=romanize_caption(stop.reason), estimated_cost_min=low,
            estimated_cost_max=high, cost_source=cost_source, rating=place.get("rating"),
            rating_count=place.get("userRatingCount"), maps_url=maps_link(place),
            travel_to_next=travel, verification_status="verified_place" if place else "unverified",
            warnings=stop_warnings,
            attributions=list(dict.fromkeys(
                item["provider"][:110] for item in place.get("attributions", [])
                if item.get("provider")
            ))[:4],
        ))

    cost_min = sum(stop.estimated_cost_min for stop in activities)
    cost_max = sum(stop.estimated_cost_max for stop in activities)
    transport_min, transport_max = ride_cost(routes, mode, people)
    contingency = math.ceil((cost_max + transport_max) * 0.10)
    total_min = cost_min + transport_min + contingency
    total_max = cost_max + transport_max + contingency
    if total_max > prefs.budget:
        errors.append(f"Upper estimated cost ₹{total_max} exceeds the ₹{prefs.budget} budget.")
    travel_minutes = sum(route.duration_minutes or 0 for route in routes)
    buffer = duration - travel_minutes - sum(stop.duration_minutes for stop in activities)
    totals = {
        "estimated_cost_min": total_min,
        "estimated_cost_max": total_max,
        "transport_cost_min": transport_min,
        "transport_cost_max": transport_max,
        "contingency": contingency,
        "remaining_budget": prefs.budget - total_max,
        "duration_minutes": duration,
        "travel_minutes": travel_minutes,
        "buffer_minutes": buffer,
    }
    if errors:
        return None, errors, {**totals, "warnings": warnings}
    weather = None
    if weather_rows:
        temp_min = round(min(row["temperature_c"] for row in weather_rows))
        temp_max = round(max(row["temperature_c"] for row in weather_rows))
        rain = max(row["rain_probability"] for row in weather_rows)
        conditions = ", ".join(dict.fromkeys(row["condition"] for row in weather_rows))
        temperature = f"{temp_min}°C" if temp_min == temp_max else f"{temp_min}–{temp_max}°C"
        weather = f"{temperature}, {conditions}. Up to {rain}% chance of precipitation during planned stops."
    else:
        warnings.append("Weather forecast is unavailable for the planned hours.")
    result = Itinerary(
        itinerary_id=state.itinerary_id, version=(state.itinerary.version if state.itinerary else 0) + 1,
        title=romanize_caption(proposal.title), language=proposal.language, city=prefs.city, date=day, budget=prefs.budget,
        starting_leg=starting_leg, activities=activities, explanation=romanize_caption(proposal.explanation),
        weather_summary=weather, assumptions=assumptions, warnings=warnings,
        validation_status="passed", verification_status="unverified" if proposal.generic else "grounded",
        maps_fallback=maps_fallback,
        **totals,
    )
    return result, [], totals


def maps_link(place: dict) -> str | None:
    if not place:
        return None
    if place.get("googleMapsUri"):
        return place["googleMapsUri"]

    from urllib.parse import urlencode

    return "https://www.google.com/maps/search/?" + urlencode({
        "api": 1,
        "query": place["formattedAddress"],
        "query_place_id": place["id"],
    })
