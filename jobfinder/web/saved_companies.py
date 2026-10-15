"""Saved Companies on the Companies page: search results you chose to keep, separate from My Companies.

Kept in user-data/saved_companies.json (yours: updates leave it alone). An entry has what the search found: name, website,
address, phone, the town the distance is measured from, a note about where it was found, and its map point.
"""
import json
import uuid
from datetime import datetime

from jobfinder import paths
from jobfinder.profiles.travel import miles_between
from jobfinder.web import my_companies

MAX_COMPANIES = 1000
FIELDS = ("name", "website", "address", "phone", "place", "notes")


def load():
    try:
        data = json.loads(paths.SAVED_COMPANIES_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [item for item in data if isinstance(item, dict) and item.get("id") and item.get("name")] if isinstance(data, list) else []


def _save(companies):
    path = paths.SAVED_COMPANIES_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(companies, indent=2), encoding="utf-8")
    temp.replace(path)


def add(fields, home_state=""):
    """Save a company. One already saved under the same name is returned as it is (second item: False)."""
    companies = load()
    for item in companies:
        if my_companies.same_company(item.get("name"), fields.get("name")):
            return item, False
    if len(companies) >= MAX_COMPANIES:
        raise ValueError(f"The list is full ({MAX_COMPANIES} companies). Remove some first.")
    values = my_companies.clean({key: fields.get(key, "") for key in FIELDS}, home_state)  # the same limits and map point
    entry = {key: values.get(key, "") for key in FIELDS + ("lat", "lon")}
    entry.update(id=uuid.uuid4().hex[:12], saved=datetime.now().astimezone().isoformat(timespec="seconds"))
    _save(companies + [entry])
    return entry, True


def delete(company_id):
    companies = load()
    kept = [item for item in companies if item["id"] != company_id]
    if len(kept) == len(companies):
        raise KeyError(company_id)
    _save(kept)


def as_result(item, center=None):
    miles = None
    if center is not None and item.get("lat") is not None and item.get("lon") is not None:
        miles = round(miles_between(center, (float(item["lat"]), float(item["lon"]))), 1)
    return {"id": item["id"], "name": item["name"], "address": item.get("address", ""), "website": item.get("website", ""),
            "phone": item.get("phone", ""), "place": item.get("place", ""), "notes": item.get("notes", ""), "miles": miles}


def everything(center=None):
    """Nearest first when a center is given, then the ones that can't be measured, each by name."""
    results = [as_result(item, center) for item in load()]
    measured = sorted((r for r in results if r["miles"] is not None), key=lambda r: (r["miles"], r["name"].casefold()))
    unmeasured = sorted((r for r in results if r["miles"] is None), key=lambda r: r["name"].casefold())
    return measured + unmeasured
