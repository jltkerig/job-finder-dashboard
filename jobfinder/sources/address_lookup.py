"""Address suggestions as you type, from OpenStreetMap through Photon (photon.komoot.io, a search-as-you-type service).

The Companies page asks for these while an address is typed. Nominatim, the service the nearby-companies search uses, does
not allow search-as-you-type on its public servers, which is why this uses Photon. No key is needed. Each question goes
out at most once a second, answers are kept for an hour, and a service that does not answer just gives no suggestions.
The data is OpenStreetMap contributors' (ODbL): an address is only suggested when somebody has mapped it.
"""

import time

import requests

from jobfinder.profiles.places import STATE_NAMES

ENDPOINT = "https://photon.komoot.io/api/"
USER_AGENT = "JobFinder/1.0 (personal job search tool; address suggestions)"
TIMEOUT_SECONDS = 8
GAP_SECONDS = 1.0
CACHE_SECONDS = 3600
ASKED = 10  # answers asked for; the ones outside the U.S. or without a street are dropped, up to SHOWN are kept
SHOWN = 6
UNITED_STATES = "-179.2,18.8,-66.9,71.5"  # Photon's bbox: left,bottom,right,top
STATE_BY_NAME = {name.casefold(): code for code, name in STATE_NAMES.items()}
BUSINESS_KEYS = {"amenity", "shop", "office", "craft", "healthcare"}  # an answer that is a business, not a building or a road

_cache = {}  # (typed text, rounded bias point) -> (when, suggestions)
_last_request = [0.0]


def clear_cache():
    _cache.clear()
    _last_request[0] = 0.0


def _text(value):
    return " ".join(str(value or "").split())


def format_features(features):
    """Suggestions from Photon's answer: [{"address", "place", "name", "detail"}]. Only places in the U.S. with a street are
    kept (a town or a bus stop is not an address); the same address is listed once."""
    found, seen = [], set()
    for feature in features or []:
        properties = (feature or {}).get("properties") or {}
        street = _text(properties.get("street"))
        if str(properties.get("countrycode") or "").upper() != "US" or not street:
            continue
        line = " ".join(part for part in (_text(properties.get("housenumber")), street) if part)
        town = _text(properties.get("city") or properties.get("locality") or properties.get("district") or properties.get("county"))
        state = _text(properties.get("statecode")) or STATE_BY_NAME.get(_text(properties.get("state")).casefold(), _text(properties.get("state")))
        postcode = _text(properties.get("postcode"))
        region = " ".join(part for part in (state, postcode) if part)
        postcode = postcode[:5]
        address = ", ".join(part for part in (line, town, region) if part)
        key = address.casefold()
        if key in seen:
            continue
        seen.add(key)
        name = _text(properties.get("name"))
        business = name if name and properties.get("osm_key") in BUSINESS_KEYS and name.casefold() != street.casefold() else ""
        found.append({"street": line, "city": town, "state": state, "zip": postcode, "address": address, "place": ", ".join(part for part in (town, state) if part) if town and state else "",
                      "name": business, "detail": name if name and name.casefold() != street.casefold() else ""})
        if len(found) >= SHOWN:
            break
    return found


def suggest(typed, near=None, http=requests.get, sleep=time.sleep, now=time.time):
    """Addresses that start like `typed`, nearest `near` ((lat, lon), usually home) first. [] when nothing is found;
    raises ValueError with a message when the service cannot be reached."""
    typed = _text(typed)
    if len(typed) < 4:
        return []
    bias = (round(near[0], 2), round(near[1], 2)) if near else None
    key = (typed.casefold(), bias)
    saved = _cache.get(key)
    if saved and now() - saved[0] < CACHE_SECONDS:
        return saved[1]
    wait = GAP_SECONDS - (now() - _last_request[0])
    if wait > 0:
        sleep(wait)
    params = {"q": typed, "limit": ASKED, "lang": "en", "bbox": UNITED_STATES}
    if bias:
        params.update(lat=bias[0], lon=bias[1])
    _last_request[0] = now()
    try:
        response = http(ENDPOINT, params=params, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT_SECONDS)
    except requests.RequestException as error:
        raise ValueError("The address service could not be reached.") from error
    if response.status_code != 200:
        raise ValueError(f"The address service answered HTTP {response.status_code}.")
    try:
        data = response.json()
    except ValueError as error:
        raise ValueError("The address service sent an answer that could not be read.") from error
    suggestions = format_features(data.get("features") if isinstance(data, dict) else [])
    _cache[key] = (now(), suggestions)
    return suggestions
