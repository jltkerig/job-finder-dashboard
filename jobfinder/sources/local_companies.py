"""Companies near a place, from OpenStreetMap (read through Nominatim, its place-search service).

The Companies page uses this to list the businesses, clinics, plants and schools around a town, with their address and
website, so you can see who is nearby whether or not they have a job posted. No key is needed; the data is OpenStreetMap
contributors' (ODbL), so it is as complete as they have made it for the area.

Each kind of place is a few words Nominatim understands ("clinic", "factory", "insurance"). One question is asked per word,
at most one a second as its usage rules require, and every answer is kept for hours so the same search is not asked again.
"""

import math
import re
import time

import requests

ENDPOINT = "https://nominatim.openstreetmap.org/search"
USER_AGENT = "JobFinder/1.0 (personal job search tool; companies near a place)"
TIMEOUT_SECONDS = 20
PER_WORD_LIMIT = 40  # the most Nominatim returns for one question
GAP_SECONDS = 1.1  # its usage rules: no more than one request a second
RETRY_WAIT_SECONDS = 5
CACHE_SECONDS = 6 * 3600

# Kind key -> (label, the words asked for). Words Nominatim has nothing for just bring back nothing, so each list holds
# words that brought back real places around a town. Shown in this order on the Companies page.
KINDS = {
    "offices": ("Offices and companies", ["company", "insurance", "office"]),
    "finance": ("Banks", ["bank"]),
    "legal": ("Legal and real estate", ["real estate", "attorney"]),
    "healthcare": ("Healthcare", ["clinic", "doctors", "dentist", "pharmacy", "hospital", "veterinary", "nursing home"]),
    "industry": ("Industry and trades", ["factory", "warehouse", "industrial", "car repair", "electrician"]),
    "construction": ("Construction and logistics", ["construction", "carpenter", "freight", "logistics", "airport"]),
    "government": ("Government and public services", ["fire station", "post office", "police", "library", "town hall", "courthouse"]),
    "education": ("Schools and colleges", ["school", "college", "university", "kindergarten"]),
    "creative": ("Design, media and tech", ["design", "studio", "gallery", "photographer", "printing", "computer"]),
    "auto": ("Cars and fuel", ["car wash", "fuel", "car repair"]),
    "personal": ("Beauty and fitness", ["hairdresser", "beauty", "spa", "fitness", "gym"]),
    "community": ("Community and care", ["community centre", "association", "daycare"]),
    "home": ("Home and garden", ["garden centre", "florist", "furniture"]),
    "retail": ("Shops", ["supermarket", "department store", "hardware"]),
    "food": ("Food and lodging", ["restaurant", "hotel", "cafe", "bakery"]),
}
DEFAULT_KINDS = ("offices", "finance", "healthcare", "industry", "education")

_cache = {}  # (rounded lat, rounded lon, miles, kind) -> (when, companies)
_last_request = [0.0]


def clear_cache():
    _cache.clear()
    _last_request[0] = 0.0


def miles_between(a, b):
    """Straight-line miles between two (lat, lon) points."""
    (lat1, lon1), (lat2, lon2) = a, b
    p1, p2 = math.radians(lat1), math.radians(lat2)
    h = (math.sin((p2 - p1) / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2)
    return 3958.8 * 2 * math.asin(math.sqrt(h))


def viewbox(lat, lon, miles):
    """Nominatim's "left,top,right,bottom" box that holds the circle of that many miles."""
    d_lat = miles / 69.0
    d_lon = miles / (69.17 * max(math.cos(math.radians(lat)), 0.2))
    return f"{lon - d_lon:.5f},{lat + d_lat:.5f},{lon + d_lon:.5f},{lat - d_lat:.5f}"


def _website(extra):
    raw = str(extra.get("website") or extra.get("contact:website") or extra.get("url") or "").strip()
    if not raw:
        return ""
    if not re.match(r"^https?://", raw, re.I):
        raw = "https://" + raw.lstrip("/")
    return raw if re.match(r"^https?://[^\s/]+\.[^\s/]+", raw, re.I) else ""


def _address(address):
    street = " ".join(part for part in (address.get("house_number"), address.get("road")) if part)
    town = next((address[key] for key in ("city", "town", "village", "hamlet", "suburb") if address.get(key)), "")
    state = str(address.get("ISO3166-2-lvl4") or "")
    state = state.split("-")[-1] if re.fullmatch(r"[A-Z]{2}-[A-Z0-9]{1,3}", state) else str(address.get("state") or "")
    line = ", ".join(part for part in (street, town, state) if part)
    if address.get("postcode"):
        line = f"{line} {address['postcode']}".strip()
    return line


def _type_label(item):
    kind = str(item.get("type") or "")
    if not kind or kind in ("yes", "unclassified"):
        kind = str(item.get("category") or "")
    return kind.replace("_", " ").capitalize()


def parse_places(items, center, miles=None):
    """Companies (nearest first) from Nominatim answers: name, type, address, website, phone, point and miles away.

    The same place found by two questions is listed once, and so is a name repeated at one address. With miles given,
    places in the corners of the search box beyond that distance are left out."""
    found, seen = [], set()
    for item in items or []:
        name = str(item.get("name") or "").strip()
        try:
            point = (float(item["lat"]), float(item["lon"]))
        except (KeyError, TypeError, ValueError):
            continue
        if not name:
            continue
        away = miles_between(center, point)
        if miles is not None and away > miles:
            continue
        extra = item.get("extratags") or {}
        address = _address(item.get("address") or {})
        key = (name.casefold(), address.casefold() or f"{point[0]:.3f},{point[1]:.3f}")
        if key in seen:
            continue
        seen.add(key)
        found.append({
            "id": f"{item.get('osm_type', 'node')}/{item.get('osm_id')}", "name": name, "type": _type_label(item),
            "address": address, "website": _website(extra), "phone": str(extra.get("phone") or extra.get("contact:phone") or ""),
            "lat": round(point[0], 6), "lon": round(point[1], 6), "miles": round(away, 1),
        })
    found.sort(key=lambda company: (company["miles"], company["name"].casefold()))
    return found


def _ask(word, box, http, sleep):
    """The answer items for one word, or raises requests.RequestException / ValueError."""
    wait = GAP_SECONDS - (time.time() - _last_request[0])
    if wait > 0:
        sleep(wait)
    params = {"q": word, "format": "jsonv2", "viewbox": box, "bounded": 1, "limit": PER_WORD_LIMIT,
              "addressdetails": 1, "extratags": 1}
    for attempt in range(2):
        _last_request[0] = time.time()
        response = http(ENDPOINT, params=params, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT_SECONDS)
        if response.status_code == 429 and attempt == 0:
            sleep(RETRY_WAIT_SECONDS)
            continue
        if response.status_code != 200:
            raise ValueError(f"OpenStreetMap answered HTTP {response.status_code}.")
        data = response.json()
        return data if isinstance(data, list) else []
    raise ValueError("OpenStreetMap's service is busy right now.")


def search(lat, lon, miles, kinds=DEFAULT_KINDS, http=None, sleep=time.sleep):
    """Companies within miles of (lat, lon) of the chosen kinds, nearest first.

    Raises ValueError with a message for the page when nothing could be read from the service."""
    kinds = tuple(key for key in kinds if key in KINDS) or DEFAULT_KINDS
    lat, lon, miles = float(lat), float(lon), float(miles)
    get = http or requests.get
    box = viewbox(lat, lon, miles)
    companies, failures, asked = [], 0, 0
    last = ""
    for kind in kinds:
        key = (round(lat, 3), round(lon, 3), round(miles, 1), kind)
        saved = _cache.get(key)
        asked += 1
        if saved and time.time() - saved[0] < CACHE_SECONDS:
            companies.extend(saved[1])
            continue
        items, kind_ok = [], False
        for word in KINDS[kind][1]:
            try:
                items.extend(_ask(word, box, get, sleep))
                kind_ok = True
            except (requests.RequestException, ValueError) as error:
                last = (f"Could not reach OpenStreetMap: {type(error).__name__}."
                        if isinstance(error, requests.RequestException) else str(error))
        if not kind_ok:
            failures += 1
            continue
        found = parse_places(items, (lat, lon), miles)
        _cache[key] = (time.time(), found)
        companies.extend(found)
    if failures == asked:
        raise ValueError((last or "OpenStreetMap's service did not answer.") + " Try again in a minute.")
    unique, seen = [], set()
    for company in sorted(companies, key=lambda c: (c["miles"], c["name"].casefold())):
        if company["id"] not in seen:
            seen.add(company["id"])
            unique.append(company)
    return unique
