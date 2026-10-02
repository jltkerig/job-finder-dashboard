"""U.S. place names for the city boxes' type-ahead: every Census place (cities, towns, and unincorporated
places such as Abingdon, MD), biggest first. data/us-places.tsv comes from the U.S. Census Bureau's 2023
Gazetteer and 2023 population estimates (public domain).
"""
import re
from functools import lru_cache
from pathlib import Path

DATA_FILE = Path(__file__).resolve().parent / "data" / "us-places.tsv"
STATE_NAMES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California", "CO": "Colorado",
    "CT": "Connecticut", "DE": "Delaware", "DC": "District of Columbia", "FL": "Florida", "GA": "Georgia",
    "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas", "KY": "Kentucky",
    "LA": "Louisiana", "ME": "Maine", "MD": "Maryland", "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota",
    "MS": "Mississippi", "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada", "NH": "New Hampshire",
    "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York", "NC": "North Carolina", "ND": "North Dakota",
    "OH": "Ohio", "OK": "Oklahoma", "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island",
    "SC": "South Carolina", "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas", "UT": "Utah", "VT": "Vermont",
    "VA": "Virginia", "WA": "Washington", "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming",
    "PR": "Puerto Rico",
}
# States next to each other, so places near home come first ("Lancaster" -> Lancaster, PA for someone in MD).
NEIGHBORS = {
    "MD": {"DE", "PA", "VA", "WV", "DC"}, "DE": {"MD", "PA", "NJ"}, "PA": {"MD", "DE", "NJ", "NY", "OH", "WV"},
    "VA": {"MD", "DC", "WV", "KY", "TN", "NC"}, "DC": {"MD", "VA"}, "NJ": {"NY", "PA", "DE"},
    "WV": {"MD", "PA", "OH", "KY", "VA"}, "NY": {"NJ", "PA", "CT", "MA", "VT"},
}


def _key(text):
    return re.sub(r"[^a-z0-9]+", " ", str(text).casefold()).strip()


@lru_cache(maxsize=1)
def _places():
    rows = []
    for line in DATA_FILE.read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#"):
            continue
        name, state, population = line.split("\t")
        rows.append((_key(name), name, state, int(population)))
    return rows  # already biggest first


def city_matches(text, home_state="", limit=12):
    """ "Lancaster" -> ["Lancaster, PA", "Lancaster, CA", ...]: names that start with what was typed (after an
    optional ", ST"), places in or next to home_state first, then by population. State names come first."""
    raw = str(text or "")
    name_part, _, state_part = raw.partition(",")
    key = _key(name_part)
    state = state_part.strip().upper()[:2]
    if len(key) < 2:
        return []
    home = home_state.strip().upper()[:2]
    near = ({home} | NEIGHBORS.get(home, set())) if home else set()
    states = [f"{full}" for code, full in STATE_NAMES.items() if _key(full).startswith(key) and not state]
    found = []
    for name_key, name, place_state, population in _places():
        if state and not place_state.startswith(state):
            continue
        if name_key.startswith(key):
            found.append((place_state not in near, name_key != key, -population, f"{name}, {place_state}"))
    found.sort()
    return (states + [label for *_, label in found])[:limit]
