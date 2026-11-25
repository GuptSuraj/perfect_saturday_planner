import asyncio
import math
import os
from datetime import date

import httpx

from schemas import Coordinates, Mode, Route

SEARCH_FIELDS = (
    "id", "displayName", "formattedAddress", "location", "rating", "userRatingCount",
    "priceLevel", "priceRange", "businessStatus", "googleMapsUri", "primaryType", "types",
    "attributions",
)
DETAIL_FIELDS = (
    *SEARCH_FIELDS, "regularOpeningHours", "currentOpeningHours", "websiteUri",
    "servesVegetarianFood", "outdoorSeating", "liveMusic", "accessibilityOptions",
)
CITY_ALIASES = {
    "bangalore": "Bengaluru",
    "bombay": "Mumbai",
    "calcutta": "Kolkata",
    "cochin": "Kochi",
    "gurgaon": "Gurugram",
    "madras": "Chennai",
    "mysore": "Mysuru",
    "pondicherry": "Puducherry",
    "poona": "Pune",
    "trivandrum": "Thiruvananthapuram",
}


class ProviderError(Exception):
    """A short provider failure safe to return in a tool result."""


class Providers:
    def __init__(self, client: httpx.AsyncClient, maps_key: str | None = None):
        self.client = client
        self.maps_key = maps_key if maps_key is not None else os.getenv("GOOGLE_MAPS_API_KEY")

    async def _request(self, label: str, method: str, url: str, **kwargs) -> dict:
        for attempt in range(2):
            try:
                response = await self.client.request(method, url, **kwargs)
                response.raise_for_status()
                body = response.json()
                if not isinstance(body, dict):
                    raise ValueError("Expected object")
                return body
            except httpx.HTTPStatusError as error:
                status = error.response.status_code
                retryable = status in {408, 429} or status >= 500
                if attempt or not retryable:
                    code = "authorization" if status in {401, 403} else "request"
                    raise ProviderError(f"{label} {code} failed (HTTP {status}).") from None
            except (httpx.RequestError, ValueError):
                if attempt:
                    raise ProviderError(f"{label} did not return usable data.") from None
            await asyncio.sleep(0.25)
        raise AssertionError("All request outcomes are handled above")

    def _google_headers(self, fields: str) -> dict:
        if not self.maps_key:
            raise ProviderError("Google Maps is unavailable because its API key is missing.")
        return {"X-Goog-Api-Key": self.maps_key, "X-Goog-FieldMask": fields}

    async def places_search(
        self, query: str, city: str, locality: str | None,
        location_bias: Coordinates | None, max_results: int,
    ) -> list[dict]:
        scope = ", ".join(part for part in (locality, city, "India") if part)
        body = {
            "textQuery": f"{query} near {scope}",
            "pageSize": max_results,
            "languageCode": "en",
        }
        if location_bias is not None:
            body["locationBias"] = {
                "circle": {"center": location_bias.model_dump(), "radius": 3500.0}
            }
        result = await self._request(
            "Google Places", "POST", "https://places.googleapis.com/v1/places:searchText",
            headers=self._google_headers(",".join(f"places.{name}" for name in SEARCH_FIELDS)),
            json=body,
        )
        return [
            place for place in result.get("places", [])
            if place.get("id") and place.get("displayName", {}).get("text")
            and place.get("formattedAddress")
        ][:max_results]

    async def place_details(self, place_id: str) -> dict:
        # The caller accepts only IDs already returned by Google Places.
        return await self._request(
            "Google Place Details", "GET", f"https://places.googleapis.com/v1/places/{place_id}",
            headers=self._google_headers(",".join(DETAIL_FIELDS)),
            params={"languageCode": "en"},
        )

    async def city_near(self, coordinates: Coordinates) -> str | None:
        data = await self._request(
            "Google Places", "POST", "https://places.googleapis.com/v1/places:searchNearby",
            headers=self._google_headers("places.addressComponents"),
            json={
                "languageCode": "en", "rankPreference": "DISTANCE", "maxResultCount": 3,
                "locationRestriction": {"circle": {"center": coordinates.model_dump(), "radius": 600}},
            },
        )
        for place in data.get("places", []):
            components = {kind: component for component in place.get("addressComponents", [])
                          for kind in component.get("types", [])}
            if components.get("country", {}).get("shortText") != "IN":
                continue
            for kind in ("locality", "postal_town"):
                if city := components.get(kind, {}).get("longText"):
                    return city
        return None

    async def geocode(self, city: str) -> Coordinates:
        data = await self._request(
            "Open-Meteo geocoding", "GET", "https://geocoding-api.open-meteo.com/v1/search",
            params={"name": CITY_ALIASES.get(city.casefold(), city), "count": 5, "language": "en", "countryCode": "IN"},
        )
        for place in data.get("results", []):
            if place.get("country_code") == "IN":
                return Coordinates(latitude=place["latitude"], longitude=place["longitude"])
        raise ProviderError("Open-Meteo could not find that city in India.")

    async def forecast(self, coordinates: Coordinates, day: date) -> dict:
        data = await self._request(
            "Open-Meteo", "GET", "https://api.open-meteo.com/v1/forecast",
            params={
                **coordinates.model_dump(), "timezone": "Asia/Kolkata",
                "start_date": day.isoformat(), "end_date": day.isoformat(),
                "hourly": "temperature_2m,precipitation_probability,weather_code",
            },
        )
        hourly = data.get("hourly", {})
        rows = [
            {"time": t[-5:], "temperature_c": temp, "rain_probability": rain, "condition": weather_condition(code)}
            for t, temp, rain, code in zip(
                hourly.get("time", []), hourly.get("temperature_2m", []),
                hourly.get("precipitation_probability", []), hourly.get("weather_code", []),
            )
            if temp is not None and rain is not None and code is not None
        ]
        if not rows:
            raise ProviderError("Open-Meteo has no hourly forecast for that date.")
        return {"status": "ok", "date": day.isoformat(), "source": "Open-Meteo", "hourly": rows}

    async def route(
        self, origin: dict, destination: dict, mode: Mode,
        origin_coordinates: dict | None, destination_coordinates: dict | None,
    ) -> Route:
        try:
            result = await self._request(
                "Google Routes", "POST", "https://routes.googleapis.com/directions/v2:computeRoutes",
                headers=self._google_headers("routes.distanceMeters,routes.duration"),
                json={
                    "origin": origin, "destination": destination,
                    "travelMode": "WALK" if mode == "walk" else "DRIVE",
                },
            )
            route = result.get("routes", [])[0]
            elapsed = float(route["duration"].removesuffix("s"))
            return Route(
                distance_metres=route.get("distanceMeters", 0),
                duration_minutes=max(1, math.ceil(elapsed / 60)), mode=mode,
                source="Google Routes", verification_status="route_estimate",
            )
        except (ProviderError, IndexError, KeyError, ValueError):
            if not origin_coordinates or not destination_coordinates:
                return Route(mode=mode, source="Google Routes unavailable", verification_status="unavailable")
            distance = direct_distance(origin_coordinates, destination_coordinates)
            road_factor = 1.5
            speed = 65 if mode == "walk" else 335  # metres per minute
            extra = 2 if mode == "walk" else 8
            return Route(
                distance_metres=math.ceil(distance * road_factor),
                duration_minutes=max(3, math.ceil(distance * road_factor / speed) + extra),
                mode=mode, source="Approximation from straight-line distance",
                verification_status="approximate",
            )


def direct_distance(origin: dict, destination: dict) -> float:
    a, b = (math.radians(point["latitude"]) for point in (origin, destination))
    lon_delta = math.radians(destination["longitude"] - origin["longitude"])
    value = math.sin((a - b) / 2) ** 2 + math.cos(a) * math.cos(b) * math.sin(lon_delta / 2) ** 2
    return 12_742_000 * math.asin(min(1, math.sqrt(value)))


def weather_condition(code: int) -> str:
    if code == 0:
        return "clear"
    if code <= 2:
        return "partly cloudy"
    if code == 3:
        return "overcast"
    if code < 50:
        return "fog"
    if code >= 95:
        return "thunderstorms"
    if 71 <= code <= 77 or code in {85, 86}:
        return "snow"
    return "rain or showers"
