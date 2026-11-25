import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from schemas import Itinerary, Preferences, Route

Publish = Callable[[str, dict[str, Any]], Awaitable[bool]]


@dataclass
class PlannerState:
    publish: Publish
    preferences: Preferences = field(default_factory=Preferences)
    places: dict[str, dict] = field(default_factory=dict)
    place_references: dict[str, str] = field(default_factory=dict)
    searches: dict[tuple, list[str]] = field(default_factory=dict)
    details: set[str] = field(default_factory=set)
    routes: dict[tuple, Route] = field(default_factory=dict)
    weather: dict[tuple, dict] = field(default_factory=dict)
    itinerary: Itinerary | None = None
    itinerary_id: str = field(default_factory=lambda: uuid4().hex)
    revision_needed: bool = False
    last_places_failure: str | None = None
    trace: list[dict] = field(default_factory=list)
    writes: asyncio.Lock = field(default_factory=asyncio.Lock)
    calls: int = 0

    async def record_step(
        self, tool: str, status: str, description: str, *, event_id: str | None = None, duration_ms: int | None = None,
    ) -> None:
        event = {"id": event_id or uuid4().hex, "tool": tool, "status": status, "description": description}
        if duration_ms is not None:
            event["duration_ms"] = duration_ms
        self.trace.append(event)
        await self.publish("trace", event)

    @property
    def city_key(self) -> str:
        return (self.preferences.city or "").casefold()

    @property
    def locality_key(self) -> tuple[str, str]:
        return (self.city_key, (self.preferences.locality or "").casefold())
