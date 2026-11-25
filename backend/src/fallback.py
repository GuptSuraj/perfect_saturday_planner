from datetime import datetime
from itertools import combinations
from math import ceil

from schemas import INDIA, Itinerary, Language, MapsFailure, Preferences, Proposal, ProposalStop, clock
from state import PlannerState
from validation import activity_cost, generic_leg, plan_date, ride_cost, validate


def parse_user_preferences(preferences: Preferences) -> Preferences | None:
    # Both chat and voice already extract structured preferences before their first lookup.
    if preferences.city is None or preferences.budget is None or preferences.available_minutes is None:
        return None
    return preferences


def get_activity_options(city: str, interests: list[str]) -> list[ProposalStop]:
    options = [
        (("histor", "heritage", "itihaas"), "Self-guided heritage walk", True, 50,
         "Choose a publicly accessible heritage street and pause at two architectural details.",
         "Adds a short history stop you can explore at your own pace."),
        (("music", "sangeet", "gaana"), "Make a local music playlist", False, 35,
         "Listen to a few tracks by local artists somewhere quiet. Use headphones in shared spaces.",
         "Makes room for your interest in music on a flexible schedule."),
        (("walk", "nature", "outdoor", "park", "paidal"), "Neighbourhood park walk", True, 45,
         "Pick a free public path. Walk an easy loop with a bench break halfway through.",
         "Keeps the route flexible and gives you a chance to pause or turn back early."),
        (("read", "book", "art", "gallery"), "Short reading or sketching break", False, 40,
         "Bring a book or notebook and choose a free indoor place where visitors can sit.",
         "Fits a low-cost creative break and leaves time for the rest of the day."),
    ]
    preference = " ".join(interests).casefold()
    options.sort(key=lambda option: not any(keyword in preference for keyword in option[0]))
    return [ProposalStop(
        generic_activity=name, category="browse", outdoors=outdoors, duration_minutes=duration,
        description=f"{description} Keep it near your other stops in {city[:60]}.", reason=reason,
    ) for _, name, outdoors, duration, description, reason in options]


def get_food_options(city: str, budget: int, preferences: Preferences) -> list[ProposalStop]:
    requirements = " ".join(preferences.constraints).casefold()
    vegetarian = preferences.dietary in {"vegetarian_options", "vegetarian_only"} or (
        "non-veg" not in requirements and any(word in requirements for word in ("vegetarian", "shakahari", "शाकाहारी"))
    )
    options = []
    needs_home_food = preferences.dietary == "requires_confirmation" or any(
        word in requirements for word in ("vegan", "allerg", "gluten", "halal", "kosher")
    )
    if budget >= 550 and not needs_home_food and preferences.environment != "outdoors":
        name = "Vegetarian lunch break" if vegetarian else "Casual local lunch"
        requirement = "Check that it has a fully vegetarian kitchen. " if preferences.dietary == "vegetarian_only" else ""
        options.append(ProposalStop(
            generic_activity=name, category="food", outdoors=False, duration_minutes=50,
            description=f"Choose an inexpensive spot in the same area of {city[:60]}. {requirement}Check the menu and prices first.",
            reason="Keeps a meal in the plan with a capped spending allowance.",
        ))
    options.append(ProposalStop(
        generic_activity="Packed meal break", category="browse", outdoors=preferences.environment == "outdoors",
        duration_minutes=30,
        description="Bring food you already have that meets your dietary needs. Pick a nearby spot that allows outside food.",
        reason="A packed meal gives you control over ingredients and keeps purchases optional.",
    ))
    return options


def estimate_cost(stops: tuple[ProposalStop, ...], prefs: Preferences) -> int:
    """Use the same activity ranges, travel allowance and contingency as validation."""
    people = prefs.people or 1
    mode = prefs.transport or "walk"
    routes = [generic_leg(mode)] * (len(stops) - 1 + bool(prefs.locality))
    costs = sum(activity_cost({}, stop.category, people)[1] for stop in stops if stop.category != "browse")
    costs += ride_cost(routes, mode, people)[1]
    return costs + ceil(costs * 0.10)


def validate_plan(state: PlannerState, proposal: Proposal, maps_failure: MapsFailure) -> Itinerary | None:
    return validate(state, proposal, maps_fallback=maps_failure)[0]


async def generate_final_plan(state: PlannerState, maps_failure: MapsFailure, language: Language) -> Itinerary | None:
    prefs = parse_user_preferences(state.preferences)
    if prefs is None:
        return None
    await state.record_step("sample_preferences", "success", "Used your city, budget, time and preferences.")
    activities = get_activity_options(prefs.city, prefs.interests)
    food = get_food_options(prefs.city, prefs.budget // (prefs.people or 1), prefs)
    await state.record_step("sample_options", "success", "Selected sample activity and food options.")

    departure = None
    now = datetime.now(INDIA)
    if plan_date(state) == now.date() and prefs.start_time is None:
        next_slot = ((now.hour * 60 + now.minute) // 15 + 1) * 15
        if next_slot >= 1440:
            return None
        departure = clock(max(next_slot, 600 if prefs.available_minutes >= 360 else 900))

    mode = prefs.transport or "walk"
    transfer = generic_leg(mode).duration_minutes
    buffer = min(10, max(0, prefs.available_minutes - 10))
    relaxed = any(word in (prefs.mood or "").casefold() for word in ("tired", "relax", "thak"))
    max_stops = 3 if prefs.available_minutes >= 180 and not relaxed else 2
    activities = [stop for stop in activities if (
        prefs.environment is None or stop.outdoors == (prefs.environment == "outdoors")
    )]
    # Try a compact itinerary with a meal first, then drop a stop if needed to fit.
    for count in range(max_stops, 0, -1):
        choices = [group + (meal,) for group in combinations(activities, count - 1) for meal in food]
        if count == 1:
            choices = [(stop,) for stop in activities] + choices
        for stops in choices:
            ceiling = estimate_cost(stops, prefs)
            if ceiling > prefs.budget:
                continue
            travel = transfer * (len(stops) - 1 + bool(prefs.locality))
            visit_limit = (prefs.available_minutes - travel - buffer) // len(stops)
            if visit_limit < 10:
                continue
            proposal = Proposal(
                title=f"Sample Saturday in {prefs.city}"[:100], language=language, departure_time=departure,
                explanation="Keep the stops in one area. The times and spending are planning allowances because live location checks failed.",
                generic=True, end_buffer_minutes=buffer,
                activities=[stop.model_copy(update={"duration_minutes": min(stop.duration_minutes, visit_limit)}) for stop in stops],
            )
            if plan := validate_plan(state, proposal, maps_failure):
                await state.record_step("estimate_cost", "success", f"Estimated up to ₹{plan.estimated_cost_max}, including contingency.")
                await state.record_step("validate_itinerary", "success", "Checked the sample schedule and budget.")
                return plan
    return None
