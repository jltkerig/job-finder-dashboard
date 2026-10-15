"""Suggest the local governments around a place that post their own jobs on NEOGOV (governmentjobs.com/careers/<agency>).

The place's county, town and state come from the Census geographies service. For each, the likely NEOGOV agency names
are tried (a real one stays on /careers/<name>, an unknown one is sent to the home page), so only governments that
really have a job page are suggested. Nothing is added anywhere until the user chooses to watch one.
"""
import re
from concurrent.futures import ThreadPoolExecutor

import requests

CENSUS = "https://geocoding.geo.census.gov/geocoder/geographies/coordinates"
NEOGOV = "https://www.governmentjobs.com/careers/{slug}"
USER_AGENT = "Mozilla/5.0 (compatible; JobFinderLocal/1.0)"
TIMEOUT = 15
_areas, _probes = {}, {}


def clear_cache():
    _areas.clear()
    _probes.clear()


def area_at(point, get=requests.get):
    """{state, state_code, county, town} for a (lat, lon), or None. `county` is "Harford", `town` is "Bel Air"."""
    key = (round(point[0], 2), round(point[1], 2))
    if key in _areas:
        return _areas[key]
    area = None
    try:
        reply = get(CENSUS, params={"x": point[1], "y": point[0], "benchmark": "Public_AR_Current", "vintage": "Current_Current",
                                    "layers": "States,Counties,Incorporated Places", "format": "json"},
                    headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
        geo = reply.json()["result"]["geographies"]
        first = lambda name: (geo.get(name) or [{}])[0]  # noqa: E731
        state, county, town = first("States"), first("Counties"), first("Incorporated Places")
        if state.get("NAME"):
            area = {"state": state["NAME"], "state_code": state.get("STUSAB", ""), "county": county.get("BASENAME", ""),
                    "county_name": county.get("NAME", ""), "town": town.get("BASENAME", "")}
    except (requests.RequestException, ValueError, KeyError, TypeError, IndexError):
        return None  # not cached: it may work next time
    _areas[key] = area
    return area


def _squash(text):
    return re.sub(r"[^a-z0-9]", "", str(text or "").casefold())


def candidates(area):
    """[(kind, [agency names to try])]: the county, the town and the state, each with the spellings NEOGOV agencies use."""
    st = _squash(area.get("state_code"))
    state = _squash(area.get("state"))
    county, town = _squash(area.get("county")), _squash(area.get("town"))
    found = []
    if county:
        found.append(("County", [f"{county}county", f"{county}countymd".replace("md", st) if st else f"{county}county",
                                 f"{county}county{st}", f"countyof{county}", f"{county}", f"{county}{st}", f"{county}co"]))
    if town:
        found.append(("Town", [f"cityof{town}", f"townof{town}", f"{town}{st}", f"cityof{town}{st}", f"townof{town}{st}",
                               f"{town}", f"villageof{town}"]))
    if state:
        found.append(("State", [state, f"stateof{state}", f"{state}state", f"{st}"]))
    return [(kind, list(dict.fromkeys(slug for slug in slugs if len(slug) >= 3))) for kind, slugs in found]


def probe(slug, get=requests.get):
    """The agency's display name when governmentjobs.com has a job page for this agency name, else None."""
    if slug in _probes:
        return _probes[slug]
    try:
        reply = get(NEOGOV.format(slug=slug), headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT, allow_redirects=True)
    except requests.RequestException:
        return None  # not cached
    name = None
    if reply.status_code == 200 and re.search(rf"/careers/{re.escape(slug)}(?:[/?#]|$)", reply.url, re.I):
        title = re.search(r"<title>([^<]*)</title>", reply.text, re.I)
        parts = [part.strip() for part in (title.group(1) if title else "").split("|")]
        text = parts[-1] if parts and parts[-1] else slug
        # "Career Opportunities at Baltimore County" -> "Baltimore County"; "State of Delaware Careers" -> "State of Delaware"
        name = re.sub(r"^(?:career opportunities|jobs|careers|employment)\s+(?:at|with)\s+", "", text, flags=re.I)
        name = re.sub(r"\s+(?:careers|jobs|job opportunities|employment)$", "", name, flags=re.I).strip() or slug
    _probes[slug] = name
    return name


def suggestions(point, get=requests.get):
    """[{kind, name, agency, url}] of the governments around this point that have a NEOGOV job page, county first.
    Returns None when the place's county couldn't be looked up."""
    area = area_at(point, get)
    if area is None:
        return None
    tries = candidates(area)
    slugs = [slug for _, options in tries for slug in options]
    with ThreadPoolExecutor(max_workers=8) as pool:
        names = dict(zip(slugs, pool.map(lambda slug: probe(slug, get), slugs)))
    found, seen = [], set()
    for kind, options in tries:
        for slug in options:  # the first spelling that exists is the agency for that government
            if names.get(slug) and names[slug].casefold() not in seen:
                seen.add(names[slug].casefold())
                shown = f"State of {area['state']}" if kind == "State" else names[slug]
                found.append({"kind": kind, "name": shown, "agency": slug, "url": NEOGOV.format(slug=slug)})
                break
    return {"area": {key: area[key] for key in ("state", "county_name", "town")}, "found": found}
