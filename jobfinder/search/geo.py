"""Where a job is, and how far that is from the cities you chose."""

import json
import math
import re
import time

from bs4 import BeautifulSoup
import requests

from jobfinder.search.shared import BASE_DIR
from jobfinder.search.usa_location import US_STATES, US_STATE_ABBREVIATIONS, has_location_cue, page_body_text


NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"


def _read_app_version():
    """APP_VERSION lives in dashboard.py, where start.ps1 and update.ps1 also read it."""
    try:
        source = (BASE_DIR / "dashboard.py").read_text(encoding="utf-8")
    except OSError:
        return "unknown"
    match = re.search(r'^APP_VERSION\s*=\s*["\']([0-9]+\.[0-9]+\.[0-9]+)["\']', source, re.M)
    return match.group(1) if match else "unknown"


JOB_FINDER_VERSION = _read_app_version()


_last_nominatim_request = 0.0


_failed_geocode_queries = set()


def _normalize_geocode_query(value):
    # The suffix retires cached answers saved before places were ranked by kind (see pick_place).
    return re.sub(r"\s+", " ", (value or "").strip()).lower()[:240] + " [v2]"


# How much a kind of place looks like the town or city a person means (lower is better).
_PLACE_KIND_RANK = {"city": 0, "town": 0, "administrative": 0, "municipality": 1, "borough": 1, "suburb": 2,
                    "census_designated_place": 2, "village": 3, "hamlet": 4, "statistical": 5, "neighbourhood": 5}


def pick_place(results):
    """The best match among several: a real city or town beats a village or statistical area of the same name.

    Nominatim ranked a tiny "Bel Air" in Allegany County above Bel Air in Harford County, 100 miles away.
    """
    return min(results, key=lambda item: (_PLACE_KIND_RANK.get(item.get("type"), 4), -float(item.get("importance") or 0)))


def _place_name(value):
    value = re.sub(r"\bst\.? ", "saint ", str(value or "").strip().casefold())
    value = re.sub(r"\bmt\.? ", "mount ", value)
    return re.sub(r"\bft\.? ", "fort ", value)


def place_matches(query, display_name):
    """True when the map result is the place that was asked for, not a road or a similar name elsewhere."""
    city = _place_name((query or "").split(",")[0])
    first = _place_name((display_name or "").split(",")[0])
    if not city or not first:
        return False
    suffixes = ("city", "town", "village", "township", "borough", "county", "cdp")
    return (first == city or first in {f"{city} {suffix}" for suffix in suffixes}
            or first in {f"city of {city}", f"town of {city}", f"village of {city}"})


def geocode_location(database, query, require_place_match=False):
    """Geocode once with public Nominatim, then reuse the MySQL cache.

    require_place_match rejects results that are not the named place, such as a road
    called "New London Road" when the query was "London".
    """
    global _last_nominatim_request
    key = _normalize_geocode_query(query)
    if not key:
        return None
    if key in _failed_geocode_queries:
        return None

    cursor = database.cursor(dictionary=True)
    try:
        cursor.execute("SELECT latitude, longitude, display_name FROM geocode_cache WHERE query_text = %s", (key,))
        cached = cursor.fetchone()
        if cached:
            if require_place_match and not place_matches(query, cached.get("display_name")):
                return None
            return {"lat": float(cached["latitude"]), "lon": float(cached["longitude"]), "display_name": cached.get("display_name")}
    finally:
        cursor.close()

    elapsed = time.monotonic() - _last_nominatim_request
    if elapsed < 1.05:
        time.sleep(1.05 - elapsed)

    try:
        response = requests.get(
            NOMINATIM_URL,
            params={"q": query, "format": "jsonv2", "limit": 5, "countrycodes": "us"},
            headers={"User-Agent": f"JobFinder/{JOB_FINDER_VERSION} (https://github.com/jltkerig/job-finder-dashboard)"},
            timeout=15,
        )
        _last_nominatim_request = time.monotonic()
        response.raise_for_status()
        results = response.json()
        if not results:
            _failed_geocode_queries.add(key)
            return None
        result = pick_place(results)
        lat, lon = float(result["lat"]), float(result["lon"])
        display_name = result.get("display_name", "")[:1000]
        cursor = database.cursor()
        try:
            cursor.execute(
                "INSERT INTO geocode_cache (query_text, latitude, longitude, display_name) VALUES (%s, %s, %s, %s) ON DUPLICATE KEY UPDATE latitude=VALUES(latitude), longitude=VALUES(longitude), display_name=VALUES(display_name)",
                (key, lat, lon, display_name),
            )
            database.commit()
        finally:
            cursor.close()
        if require_place_match and not place_matches(query, display_name):
            print(f"Geocode for {query} rejected: it resolved to {display_name[:60]}, not that place.")
            return None
        return {"lat": lat, "lon": lon, "display_name": display_name}
    except (requests.RequestException, ValueError, KeyError) as error:
        _failed_geocode_queries.add(key)
        print(f"Geocoding skipped for {query}: {error}")
        return None


def haversine_miles(lat1, lon1, lat2, lon2):
    radius = 3958.7613
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return radius * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def extract_job_city(html, fallback_text="", allow_footer=False):
    soup = BeautifulSoup(html or "", "html.parser")
    json_objects = []
    for tag in soup.find_all("script", attrs={"type": "application/ld+json"}):
        try:
            data = json.loads(tag.string or tag.get_text() or "null")
        except (json.JSONDecodeError, TypeError):
            continue
        json_objects.append(data)

    def iter_objects(value):
        if isinstance(value, dict):
            yield value
            for child in value.values():
                yield from iter_objects(child)
        elif isinstance(value, list):
            for child in value:
                yield from iter_objects(child)

    # Prefer JobPosting.jobLocation over unrelated company/legal addresses.
    for data in json_objects:
        for obj in iter_objects(data):
            obj_type = obj.get("@type")
            types = obj_type if isinstance(obj_type, list) else [obj_type]
            if "JobPosting" not in types:
                continue
            locations = obj.get("jobLocation") or obj.get("applicantLocationRequirements")
            for location in iter_objects(locations):
                address = location.get("address") if isinstance(location, dict) else None
                if isinstance(address, dict):
                    locality = str(address.get("addressLocality", "")).strip()
                    region = str(address.get("addressRegion", "")).strip()
                    if locality:
                        return locality, region

    # Fall back to structured addresses only if no JobPosting location exists.
    for data in json_objects:
        for obj in iter_objects(data):
            address = obj.get("address") if isinstance(obj, dict) else None
            if isinstance(address, dict):
                locality = str(address.get("addressLocality", "")).strip()
                region = str(address.get("addressRegion", "")).strip()
                if locality:
                    return locality, region

    # No structured address: look for "City, ST" in the listing's location first, then the page body.
    # A ZIP code or "Location:" wording before the city beats a name like "Contact Jane Doe, MD".
    # The whole page, footer included, is kept for the last-resort address lookup below (the next call strips it).
    full_text = soup.get_text(" ", strip=True) if allow_footer else ""
    body = page_body_text(soup)
    # "Washington, D.C. 20006" is the District's usual spelling; read it as "DC".
    body = re.sub(r"\bD\.\s?C\.?(?=\s*\d{5}\b|\s|$)", "DC", body)
    leading_noise = {"contact", "email", "call", "address", "location", "located", "office", "offices", "visit", "our",
                     "at", "in", "based", "dr", "mr", "ms", "mrs", "team", "meet", "join", "apply", "posted",
                     "nw", "ne", "sw", "se", "north", "south", "east", "west"}
    candidates = []
    for source, text in ((0, str(fallback_text or "")), (1, body)):
        for match in re.finditer(r"\b([A-Z][A-Za-z'.]+(?:[ -][A-Z][A-Za-z'.]+){0,2}),\s*([A-Z]{2})\b(\s+\d{5})?", text):
            if match.group(2) not in US_STATE_ABBREVIATIONS and match.group(2) != "DC":
                continue
            words = match.group(1).split(" ")
            while words and words[0].casefold().rstrip(".") in leading_noise:
                words.pop(0)
            if not words:
                continue
            rank = (source, 0 if match.group(3) else 1, 0 if has_location_cue(text[:match.start()]) else 1, match.start())
            candidates.append((rank, " ".join(words), match.group(2)))
    if candidates:
        _, city, region = min(candidates)
        return city, region
    # On the employer's own site, the street address in the footer is where the office is: use the last
    # "City, ST 12345" on the page. (On a job board the footer is the board's address, so this is not used.)
    if allow_footer:
        full_text = re.sub(r"\bD\.\s?C\.?(?=\s*\d{5}\b)", "DC", full_text)
        addresses = [match for match in re.finditer(
            r"\b([A-Z][A-Za-z'.]+(?:[ -][A-Z][A-Za-z'.]+){0,2}),\s*([A-Z]{2})\s+\d{5}\b", full_text)
            if match.group(2) in US_STATE_ABBREVIATIONS or match.group(2) == "DC"]
        if addresses:
            last = addresses[-1]
            words = last.group(1).split(" ")
            while words and words[0].casefold().rstrip(".") in leading_noise:
                words.pop(0)
            if words:
                return " ".join(words), last.group(2)
    return None, None


_ZIP_CODE = re.compile(r"\d{5}(?:-\d{4})?")


_REGION_WORDS = re.compile(r"\b(?:greater|metropolitan|metro|area|region|metroplex)\b", re.I)


# Regions people use in place of a city, mapped to the city that anchors them.
_REGION_ALIASES = {"dmv": "Washington, DC", "dc metro": "Washington, DC", "washington dc": "Washington, DC",
                   "national capital": "Washington, DC", "tri-state": None, "delmarva": None}


def clean_region_name(place):
    """"Greater Baltimore Area" -> "Baltimore"; "DMV" -> "Washington, DC". Other places come back unchanged."""
    text = re.sub(r"\s+", " ", str(place or "")).strip()
    key = _REGION_WORDS.sub(" ", text).strip(" ,-").casefold()
    key = re.sub(r"\s+", " ", key)
    if key in _REGION_ALIASES:
        return _REGION_ALIASES[key] or text
    if _REGION_WORDS.search(text) and key:
        return re.sub(r"\s+", " ", _REGION_WORDS.sub(" ", text)).strip(" ,-")
    return text


def geocode_queries(city, state_text):
    """What to ask the geocoder for a typed location: a ZIP code, "City, ST", or a county/city with each searched state."""
    city = str(city or "").strip()
    if _ZIP_CODE.fullmatch(city):
        return [f"{city[:5]}, United States"]
    if "," in city:
        return [city]
    states = [part.strip() for part in re.split(r"[,/;]", state_text or "") if part.strip()]
    return [f"{city}, {state_name}" for state_name in states] or [city]


def prepare_city_targets(database, state, cities):
    targets = []
    allowed = {5, 10, 15, 20, 30, 50}
    for item in cities or []:
        city = str(item.get("city", "")).strip()
        try:
            radius = int(item.get("radius", 50))
        except (TypeError, ValueError):
            radius = 50
        if not city or radius not in allowed:
            continue
        if city.casefold() in US_STATES:
            continue  # A state name is a statewide target, not a city-radius center.
        point = None
        for query in geocode_queries(city, state):
            point = geocode_location(database, query)
            if point:
                break
        if point:
            targets.append({"city": city, "radius": radius, **point})
            print(f"City radius: {city} — {radius} miles")
    return targets


def distance_to_city_targets(database, html, fallback_text, state, targets, allow_footer=False):
    if not targets:
        return True, None, None, None, None
    city, detected_state = extract_job_city(html, fallback_text, allow_footer=allow_footer)
    if not city:
        return False, None, None, None, None
    city = clean_region_name(city)
    if "," in city:
        # A region alias can carry its own state ("Washington, DC").
        city, _, alias_state = city.partition(",")
        detected_state = alias_state.strip() or detected_state
    query_state = detected_state or state
    point = geocode_location(database, f"{city}, {query_state}", require_place_match=True)
    if not point:
        return False, city, None, None, None
    best_distance = None
    for target in targets:
        distance = haversine_miles(point["lat"], point["lon"], target["lat"], target["lon"])
        if best_distance is None or distance < best_distance:
            best_distance = distance
        if distance <= target["radius"]:
            return True, city, point["lat"], point["lon"], round(distance, 2)
    return False, city, point["lat"], point["lon"], round(best_distance, 2) if best_distance is not None else None
