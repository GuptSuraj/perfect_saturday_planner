from datetime import date, datetime, timedelta
from typing import Annotated, Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field

INDIA = ZoneInfo("Asia/Kolkata")
Time = Annotated[str, Field(pattern=r"^(?:[01][0-9]|2[0-3]):[0-5][0-9]$")]
ShortText = Annotated[str, Field(min_length=1, max_length=110)]
Mode = Literal["walk", "drive"]
Language = Literal["en", "hi"]
Category = Literal["food", "public_outdoors", "browse", "ticketed", "other"]


def today_in_india() -> date:
    return datetime.now(INDIA).date()


def upcoming_saturday(today: date | None = None) -> date:
    today = today or today_in_india()
    return today + timedelta(days=(5 - today.weekday()) % 7)


def minutes(value: str) -> int:
    hours, mins = map(int, value.split(":"))
    return hours * 60 + mins


def clock(value: int) -> str:
    return f"{value // 60:02}:{value % 60:02}"


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Preferences(Model):
    city: ShortText | None = None
    budget: int | None = Field(default=None, ge=0, le=1_000_000)
    available_minutes: int | None = Field(default=None, ge=15, le=960)
    mood: ShortText | None = None
    interests: list[ShortText] = Field(default_factory=list, max_length=8)
    constraints: list[ShortText] = Field(default_factory=list, max_length=8)
    locality: ShortText | None = None
    start_time: Time | None = None
    people: int | None = Field(default=None, ge=1, le=20)
    transport: Mode | None = None
    dietary: Literal["vegetarian_options", "vegetarian_only", "requires_confirmation"] | None = Field(
        default=None, description="Vegetarian or shakahari food: vegetarian_options. A pure-veg kitchen: vegetarian_only. Other dietary requirements: requires_confirmation.",
    )
    environment: Literal["indoors", "outdoors"] | None = Field(default=None, description="Use for a required indoor or outdoor setting.")
    accessibility_required: bool | None = Field(default=None, description="Set when wheelchair-accessible entry is required.")
    target_date: date | None = None
    allow_unverified_plan: bool | None = None


class Coordinates(Model):
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)


class ProposalStop(Model):
    place_id: ShortText | None = None
    generic_activity: ShortText | None = None
    category: Category
    outdoors: bool
    start_time: Time | None = None
    buffer_before_minutes: int = Field(default=0, ge=0, le=60)
    duration_minutes: int = Field(ge=10, le=480)
    description: str = Field(min_length=1, max_length=240)
    reason: str = Field(min_length=1, max_length=240)


class Proposal(Model):
    title: str = Field(min_length=1, max_length=100)
    explanation: str = Field(min_length=1, max_length=360)
    language: Language = Field(default="en", description="en for English text, hi for romanized Hindi (Hinglish).")
    activities: list[ProposalStop] = Field(min_length=1, max_length=5)
    departure_time: Time | None = None
    end_buffer_minutes: int = Field(default=10, ge=0, le=60)
    generic: bool = False


class Route(Model):
    distance_metres: int | None = None
    duration_minutes: int | None = None
    mode: Mode
    source: str
    verification_status: Literal["route_estimate", "approximate", "unavailable"]


class Transit(Route):
    destination: str


class Activity(Model):
    place_id: str | None
    name: str
    address: str | None
    start_time: str
    end_time: str
    duration_minutes: int
    description: str
    reason: str
    estimated_cost_min: int
    estimated_cost_max: int
    cost_source: str
    rating: float | None = None
    rating_count: int | None = None
    maps_url: str | None = None
    travel_to_next: Transit | None = None
    verification_status: Literal["verified_place", "unverified"]
    warnings: list[str] = Field(default_factory=list)
    attributions: list[str] = Field(default_factory=list)


class Itinerary(Model):
    itinerary_id: str
    version: int
    status: Literal["draft", "accepted"] = "draft"
    title: str
    language: Language = "en"
    city: str
    date: date
    currency: Literal["INR"] = "INR"
    budget: int
    estimated_cost_min: int
    estimated_cost_max: int
    transport_cost_min: int
    transport_cost_max: int
    contingency: int
    remaining_budget: int
    duration_minutes: int
    travel_minutes: int
    buffer_minutes: int
    starting_leg: Transit | None = None
    activities: list[Activity]
    explanation: str
    weather_summary: str | None = None
    assumptions: list[str]
    warnings: list[str]
    validation_status: Literal["passed"]
    verification_status: Literal["grounded", "unverified"]
