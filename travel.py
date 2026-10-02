"""How far a job is from home, and roughly how long the drive takes at 6 a.m.

Everything is worked out on this computer from two public-domain Census files: data/us-zips.tsv (the middle of
every ZIP code) and data/us-places.tsv (the middle of every city, town and community). Nothing is looked up online.

The drive time is an estimate: straight-line distance, stretched by 1.3 for the way roads wind, driven at typical
early-morning speeds (slow near home, faster on highways). It is good for comparing jobs, not for planning a trip.
"""
import math
import re
from functools import lru_cache
from pathlib import Path

from places import NEIGHBORS, STATE_NAMES

DATA = Path(__file__).resolve().parent / "data"
ROAD_FACTOR = 1.3
# (road miles up to, average mph at 6 a.m.): slow streets near home, then arterials, then highways.
SPEEDS = ((3, 20), (10, 30), (25, 42), (60, 52), (10**9, 58))
FULL_TO_CODE = {name.casefold(): code for code, name in STATE_NAMES.items()}


def _key(text):
    return re.sub(r"[^a-z0-9]+", " ", str(text).casefold()).strip()


@lru_cache(maxsize=1)
def _zips():
    points = {}
    for line in (DATA / "us-zips.tsv").read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#"):
            zip_code, lat, lon = line.split("\t")
            points[zip_code] = (float(lat), float(lon))
    return points


@lru_cache(maxsize=1)
def _places():
    """{name key: [(state, population, lat, lon), ...]} biggest first."""
    index = {}
    for line in (DATA / "us-places.tsv").read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#"):
            name, state, population, lat, lon = line.split("\t")
            index.setdefault(_key(name), []).append((state, int(population), float(lat), float(lon)))
    return index


def zip_point(zip_code):
    return _zips().get(re.sub(r"\D", "", str(zip_code or ""))[:5])


def place_point(text, home_state=""):
    """(lat, lon) for "Bel Air, MD", "Baltimore, Maryland, United States" or "Baltimore Metropolitan Area".

    Without a state, the biggest place of that name in or next to home_state wins, then the biggest anywhere.
    """
    text = re.sub(r"\([^)]*\)", " ", str(text or ""))
    parts = [part.strip() for part in text.split(",") if part.strip()]
    if not parts:
        return None
    name = re.sub(r"\b(?:greater|metropolitan|metro|area|region)\b", " ", parts[0], flags=re.I)
    candidates = _places().get(_key(name))
    if not candidates:
        return None
    state = ""
    if len(parts) > 1:
        second = parts[1].strip()
        state = second.upper() if second.upper() in STATE_NAMES else FULL_TO_CODE.get(second.casefold(), "")
    if state:
        match = [c for c in candidates if c[0] == state]
        if match:
            return match[0][2], match[0][3]
        return None
    home = (home_state or "").upper()
    near = ({home} | NEIGHBORS.get(home, set())) if home else set()
    best = sorted(candidates, key=lambda c: (c[0] not in near, -c[1]))[0]
    return best[2], best[3]


def miles_between(a, b):
    """Straight-line miles between two (lat, lon) points."""
    (lat1, lon1), (lat2, lon2) = a, b
    p1, p2 = math.radians(lat1), math.radians(lat2)
    h = (math.sin((p2 - p1) / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2)
    return 3958.8 * 2 * math.asin(math.sqrt(h))


def drive_minutes(straight_miles):
    """Estimated minutes behind the wheel for a trip of this straight-line length at 6 a.m."""
    road = straight_miles * ROAD_FACTOR
    minutes, done = 0.0, 0.0
    for limit, mph in SPEEDS:
        stretch = min(road, limit) - done
        if stretch > 0:
            minutes += stretch / mph * 60
            done += stretch
        if done >= road:
            break
    return minutes


def describe(home_zip, job_point=None, job_text="", home_state=""):
    """{"miles": 12, "minutes": 20, "text": "12 mi · ~20 min"} or None when home or the place isn't known."""
    home = zip_point(home_zip)
    point = job_point or place_point(job_text, home_state)
    if not home or not point:
        return None
    straight = miles_between(home, point)
    minutes = drive_minutes(straight)
    shown = max(5, int(round(minutes / 5.0)) * 5) if minutes >= 3 else max(1, int(round(minutes)))
    miles = int(round(straight))
    lasts = f"~{shown} min" if shown < 120 else f"~{shown / 60:.0f} hr"
    return {"miles": miles, "minutes": shown, "text": f"{miles:,} mi · {lasts}" if miles else f"under 1 mi · {lasts}"}
