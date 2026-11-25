import asyncio
import hashlib
import math
import os
import re
from datetime import date
from urllib.parse import urlencode

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
    def __init__(
        self, client: httpx.AsyncClient, maps_key: str | None = None,
        geoapify_key: str | None = None, tavily_key: str | None = None,
    ):
        self.client = client
        self.maps_key = maps_key if maps_key is not None else os.getenv("GOOGLE_MAPS_API_KEY")
        self.geoapify_key = geoapify_key if geoapify_key is not None else os.getenv("GEOAPIFY_API_KEY")
        self.tavily_key = tavily_key if tavily_key is not None else os.getenv("TAVILY_API_KEY")
        self._places: dict[str, dict] = {}
        self._geocodes: dict[str, Coordinates] = {}
        self.geocoding_sources: dict[str, str] = {}
        self._search_cache: dict[tuple, list[dict]] = {}

    async def _request(self, label: str, method: str, url: str, **kwargs) -> dict:
        # Location lookups are bounded so fallback fits the existing tool deadline.
        attempts = 2 if label.startswith("Open-Meteo") or (label.startswith("Google") and not self.geoapify_key) else 1
        if attempts == 1:
            kwargs.setdefault("timeout", httpx.Timeout(3, connect=2))
        for attempt in range(attempts):
            try:
                response = await self.client.request(method, url, **kwargs)
                response.raise_for_status()
                body = response.json()
                if not isinstance(body, dict):
                    raise ValueError("Expected object")
                return body
            except httpx.HTTPStatusError as error:
                status = error.response.status_code
                # Quota/auth errors are handled by the provider fallback, without retrying.
                retryable = status == 408 or status >= 500
                if attempt == attempts - 1 or not retryable:
                    code = "authorization" if status in {401, 403} else "request"
                    raise ProviderError(f"{label} {code} failed (HTTP {status}).") from None
            except (httpx.RequestError, ValueError):
                if attempt == attempts - 1:
                    raise ProviderError(f"{label} did not return usable data.") from None
            await asyncio.sleep(0.25)
        raise AssertionError("All request outcomes are handled above")

    def _google_headers(self, fields: str) -> dict:
        if not self.maps_key:
            raise ProviderError("Google Maps is unavailable because its API key is missing.")
        return {"X-Goog-Api-Key": self.maps_key, "X-Goog-FieldMask": fields}

    async def _google_places_search(
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
        records = result.get("places", [])
        if not isinstance(records, list):
            raise ProviderError("Google Places did not return usable venue data.")
        places = [
            place for place in records if isinstance(place, dict)
            and place.get("id") and isinstance(place.get("displayName"), dict)
            and place["displayName"].get("text") and place.get("formattedAddress")
        ]
        if records and not places:
            raise ProviderError("Google Places did not return usable venue records.")
        return places[:max_results]

    async def _google_place_details(self, place_id: str) -> dict:
        # Called only for Google IDs, never for Geoapify IDs.
        return await self._request(
            "Google Place Details", "GET", f"https://places.googleapis.com/v1/places/{place_id}",
            headers=self._google_headers(",".join(DETAIL_FIELDS)),
            params={"languageCode": "en"},
        )

    async def _meteo_geocode(self, city: str) -> Coordinates:
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
        # Geoapify IDs must never be sent to Google as Google place IDs.
        def google_waypoint(waypoint, coordinates):
            if coordinates:
                return {"location": {"latLng": coordinates}}
            if str(waypoint.get("placeId", "")).startswith("geoapify:"):
                raise ProviderError("Coordinates unavailable for this Geoapify venue.")
            return waypoint

        try:
            result = await self._request(
                "Google Routes", "POST", "https://routes.googleapis.com/directions/v2:computeRoutes",
                headers=self._google_headers("routes.distanceMeters,routes.duration"),
                json={
                    "origin": google_waypoint(origin, origin_coordinates),
                    "destination": google_waypoint(destination, destination_coordinates),
                    "travelMode": "WALK" if mode == "walk" else "DRIVE",
                },
            )
            route = result.get("routes", [])[0]
            return parsed_route(route["distanceMeters"], float(str(route["duration"]).removesuffix("s")), mode, "Google Routes")
        except (ProviderError, IndexError, KeyError, TypeError, ValueError):
            pass
        try:
            self._geo_key()
            origin_coordinates = origin_coordinates or await self._waypoint_coordinates(origin)
            destination_coordinates = destination_coordinates or await self._waypoint_coordinates(destination)
            a, b = origin_coordinates, destination_coordinates
            result = await self._request(
                "Geoapify Routing", "GET", "https://api.geoapify.com/v1/routing",
                params={
                    "apiKey": self.geoapify_key,
                    "waypoints": f"{a['latitude']},{a['longitude']}|{b['latitude']},{b['longitude']}",
                    "mode": "walk" if mode == "walk" else "drive", "units": "metric",
                },
            )
            route = result["features"][0]["properties"]
            return parsed_route(route["distance"], route["time"], mode, "Geoapify Routing · © OpenStreetMap contributors")
        except (ProviderError, IndexError, KeyError, TypeError, ValueError):
            if not origin_coordinates or not destination_coordinates:
                return Route(mode=mode, source="Google Routes and Geoapify unavailable", verification_status="unavailable")
            distance = direct_distance(origin_coordinates, destination_coordinates)
            road_factor = 1.5
            speed = 65 if mode == "walk" else 335
            extra = 2 if mode == "walk" else 8
            return Route(
                distance_metres=math.ceil(distance * road_factor),
                duration_minutes=max(3, math.ceil(distance * road_factor / speed) + extra),
                mode=mode, source="Approximation from straight-line distance",
                verification_status="approximate",
            )

    def _geo_key(self):
        if not self.geoapify_key:
            raise ProviderError("Geoapify is unavailable because GEOAPIFY_API_KEY is missing.")

    async def geocode(self, city: str) -> Coordinates:
        if city in self._geocodes:
            return self._geocodes[city]
        if not self.geoapify_key:
            coordinates = await self._meteo_geocode(city)
            source = "Open-Meteo geocoding"
        else:
            self._geo_key()
            result = await self._request(
                "Geoapify Geocoding", "GET", "https://api.geoapify.com/v1/geocode/search",
                params={"apiKey": self.geoapify_key, "text": CITY_ALIASES.get(city.casefold(), city),
                        "filter": "countrycode:in", "limit": 1, "format": "geojson", "lang": "en"},
            )
            try:
                place = result["features"][0]["properties"]
                if not isinstance(place, dict):
                    raise ValueError("Invalid geocoding record")
                if str(place.get("country_code", "")).casefold() != "in":
                    raise ValueError("Outside India")
                coordinates = Coordinates(latitude=place["lat"], longitude=place["lon"])
            except (IndexError, KeyError, TypeError, ValueError):
                raise ProviderError("Geoapify could not locate that address in India.") from None
            source = "Geoapify Geocoding · © OpenStreetMap contributors"
        self._geocodes[city] = coordinates
        self.geocoding_sources[city] = source
        return coordinates

    async def _waypoint_coordinates(self, waypoint: dict) -> dict:
        place = self._places.get(waypoint.get("placeId"), {})
        if place.get("location"):
            return place["location"]
        if waypoint.get("address"):
            return (await self.geocode(waypoint["address"])).model_dump()
        raise ProviderError("Coordinates unavailable for routing.")

    async def places_search(self, query, city, locality, location_bias, max_results) -> list[dict]:
        bias = (location_bias.latitude, location_bias.longitude) if location_bias else None
        cache_key = (query.casefold(), city.casefold(), (locality or "").casefold(), bias, max_results)
        if cache_key in self._search_cache:
            return self._search_cache[cache_key]
        try:
            places = await self._google_places_search(query, city, locality, location_bias, max_results)
            for place in places:
                place["data_provider"] = "Google Places"
        except ProviderError:
            places = await self._geo_places_search(query, city, locality, location_bias, max_results)
        self._places.update({place["id"]: place for place in places})
        self._search_cache[cache_key] = places
        return places

    async def _geo_places_search(
        self, query, city, locality, location_bias, max_results, *, exact_name: str | None = None,
    ) -> list[dict]:
        self._geo_key()
        center = location_bias or await self.geocode(", ".join(part for part in (locality, city, "India") if part))
        categories, name = geo_categories(query)
        params = {
            "apiKey": self.geoapify_key, "categories": categories,
            "filter": f"circle:{center.longitude},{center.latitude},3500|countrycode:in",
            "bias": f"proximity:{center.longitude},{center.latitude}",
            "limit": max_results, "lang": "en",
        }
        if exact_name or name:
            params["name"] = exact_name or name
        data = await self._request("Geoapify Places", "GET", "https://api.geoapify.com/v2/places", params=params)
        features = data.get("features")
        if not isinstance(features, list):
            raise ProviderError("Geoapify Places did not return usable venue data.")
        places = []
        for feature in features:
            place = normalize_geo_place(feature)
            if place:
                places.append(place)
        if features and not places:
            raise ProviderError("Geoapify Places returned no usable venue records in India.")
        return places[:max_results]

    async def place_details(self, place_id: str) -> dict:
        known = self._places.get(place_id, {})
        if place_id.startswith("geoapify:"):
            if not known:
                raise ProviderError("Unknown Geoapify venue; search for it first.")
            # Places already includes available OSM tags; no invented detail endpoint.
            details = dict(known)
        else:
            try:
                details = await self._google_place_details(place_id)
                details["data_provider"] = "Google Places"
            except ProviderError:
                if not known:
                    raise
                details = dict(known)
                details["detail_warning"] = "Google Place Details unavailable; only discovery data is verified."
                # Match the same venue conservatively before merging another source.
                try:
                    candidates = await self._geo_places_search(
                        known["displayName"]["text"], known["formattedAddress"], None,
                        Coordinates.model_validate(known["location"]) if known.get("location") else None, 5,
                        exact_name=known["displayName"]["text"],
                    )
                    for candidate in candidates:
                        if (normalized_name(candidate["displayName"]["text"]) == normalized_name(known["displayName"]["text"])
                            and known.get("location")
                            and direct_distance(known["location"], candidate["location"]) <= 150):
                            details = {**known, **candidate, "id": place_id,
                                       "data_provider": "Google Places + Geoapify Places",
                                       "attributions": known.get("attributions", []) + candidate["attributions"]}
                            break
                except ProviderError:
                    pass
        details = await self._supplement(details)
        self._places[place_id] = details
        return details

    async def _supplement(self, place: dict) -> dict:
        if not self.tavily_key or place.get("venue_information") is not None:
            return place
        if all(place.get(key) for key in ("websiteUri", "regularOpeningHours", "priceRange", "rating")):
            return place
        try:
            data = await self._request(
                "Tavily", "POST", "https://api.tavily.com/search",
                headers={"Authorization": f"Bearer {self.tavily_key}"},
                json={"query": f"{place['displayName']['text']} {place['formattedAddress']} official website opening hours menu prices",
                      "search_depth": "basic", "max_results": 3, "include_answer": False},
            )
            # Search snippets can be stale or refer to a similarly named branch.
            # Keep citations as evidence for the model; never promote them to verified fields.
            if not isinstance(data.get("results"), list):
                raise ProviderError("Tavily did not return usable venue information.")
            evidence = [
                {"url": row["url"], "title": str(row.get("title", ""))[:200],
                 "snippet": str(row.get("content", ""))[:1600], "verification_status": "unverified_web_context"}
                for row in data.get("results", []) if isinstance(row, dict)
                and str(row.get("url", "")).startswith("https://")
            ][:3]
            return {**place, "venue_information": evidence, "supplement_provider": "Tavily",
                    "attributions": place.get("attributions", []) + [{"provider": "Tavily web search (unverified context)"}]}
        except ProviderError:
            return {**place, "supplement_warning": "Tavily venue information unavailable."}


def normalized_name(value: str) -> str:
    return " ".join(re.findall(r"\w+", value.casefold()))


def geo_categories(query: str) -> tuple[str, str | None]:
    # Geoapify Places is category/name search, not a free-text Google search.
    mappings = [
        (r"cafe|café|coffee", "catering.cafe"),
        (r"restaurant|lunch|dinner|breakfast|food|vegetarian|vegan", "catering.restaurant"),
        (r"bakery", "commercial.food_and_drink.bakery"),
        (r"book", "commercial.books"),
        (r"museum", "entertainment.museum"),
        (r"art|gallery", "entertainment.culture.gallery"),
        (r"park|garden|walk|outdoor", "leisure.park"),
        (r"cinema|movie", "entertainment.cinema"),
        (r"shopping|mall|market", "commercial.shopping_mall"),
        (r"music|bar|pub", "catering.bar"),
        (r"attraction|sightseeing|touris", "tourism.attraction"),
    ]
    selected = list(dict.fromkeys(category for pattern, category in mappings if re.search(pattern, query, re.I)))
    return (",".join(selected), None) if selected else ("catering,commercial,entertainment,leisure,tourism", query)


def normalize_geo_place(feature: dict) -> dict | None:
    try:
        if not isinstance(feature, dict) or not isinstance(feature.get("properties"), dict):
            return None
        p = feature["properties"]
        if (str(p.get("country_code", "")).casefold() != "in"
            or not all(isinstance(p.get(key), str) and p[key].strip() for key in ("name", "place_id", "formatted"))):
            return None
        coordinates = Coordinates(latitude=p["lat"], longitude=p["lon"])
        # Geoapify IDs can exceed the unchanged tool schema's 110-character limit.
        place_id = "geoapify:" + hashlib.sha256(p["place_id"].encode()).hexdigest()[:32]
        categories = p.get("categories") or []
        if not isinstance(categories, list):
            categories = []
        categories = [category for category in categories if isinstance(category, str)]
        types = []
        for prefix, mapped in (("catering.cafe", "cafe"), ("catering.restaurant", "restaurant"),
                               ("leisure.park", "park"), ("commercial.books", "book_store"),
                               ("entertainment.museum", "museum"), ("entertainment.culture.gallery", "art_gallery")):
            if any(category.startswith(prefix) for category in categories):
                types.append(mapped)
        datasource = p.get("datasource") or {}
        raw = datasource.get("raw") if isinstance(datasource, dict) else {}
        if not isinstance(raw, dict):
            raw = {}
        address = p["formatted"]
        if not address.endswith("India"):
            address += ", India"
        result = {
            "id": place_id, "displayName": {"text": p["name"]}, "formattedAddress": address,
            "location": coordinates.model_dump(), "types": types,
            "data_provider": "Geoapify Places", "geoapify_place_id": p["place_id"],
            "attributions": [{"provider": "Geoapify · https://www.geoapify.com/"},
                             {"provider": "© OpenStreetMap contributors · https://www.openstreetmap.org/copyright"}],
        }
        if types:
            result["primaryType"] = types[0]
        website = p.get("website") or raw.get("website") or raw.get("contact:website")
        if isinstance(website, str) and website.startswith("https://"):
            result["websiteUri"] = website
        opening_hours = p.get("opening_hours") or raw.get("opening_hours")
        if opening_hours:
            result["opening_hours_text"] = str(opening_hours)
        # OSM hour expressions aren't Google periods; retain verbatim, unverified for the visit.
        if raw.get("diet:vegetarian") in {"yes", "only"}:
            result["servesVegetarianFood"] = True
        if raw.get("wheelchair") == "yes":
            result["accessibilityOptions"] = {"wheelchairAccessibleEntrance": True}
        # The unchanged frontend labels venue links Google Maps. Use a coordinate
        # search link with no Google place ID; data attribution remains Geoapify/OSM.
        result["googleMapsUri"] = "https://www.google.com/maps/search/?" + urlencode({
            "api": 1, "query": f"{coordinates.latitude},{coordinates.longitude}",
        })
        return result
    except (KeyError, TypeError, ValueError):
        return None


def parsed_route(distance, seconds, mode: Mode, source: str) -> Route:
    distance, seconds = float(distance), float(seconds)
    if not math.isfinite(distance) or not math.isfinite(seconds) or distance < 0 or seconds < 0:
        raise ValueError("Invalid route measurements")
    return Route(distance_metres=math.ceil(distance), duration_minutes=max(1, math.ceil(seconds / 60)),
                 mode=mode, source=source, verification_status="route_estimate")


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
