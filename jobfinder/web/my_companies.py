"""The companies you enter yourself on the Companies page, and the employers from your résumé.

They are kept in user-data/my_companies.json (yours: updates leave it alone). An entry has a name and, optionally, a
website, address, the town or ZIP code to measure distance from, a phone number and notes. When the town or ZIP code is a
place Job Finder knows, the entry gets a map point, so it takes its place in the nearest-first list.

The employers in your work history (filled from your résumé) are added by sync_from_work_history, once each. One you
delete is remembered in user-data/my_companies_removed.json and is not added again.
"""

import json
import re
import uuid
from datetime import datetime

from jobfinder import paths
from jobfinder.profiles.travel import miles_between, place_point, zip_point
from jobfinder.sources.ats_discovery import identify

FIELD_LIMITS = {"name": 150, "website": 200, "jobs_board": 400, "address": 200, "place": 100, "phone": 40, "notes": 500}
MAX_COMPANIES = 1000
ADDRESS_PARTS = ("street", "city", "state", "zip")


def _path():
    return paths.MY_COMPANIES_FILE


def load():
    """Every entered company, oldest first. A missing or unreadable file is an empty list."""
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [item for item in data if isinstance(item, dict) and item.get("id") and item.get("name")] if isinstance(data, list) else []


def _save(companies):
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(companies, indent=2), encoding="utf-8")
    temp.replace(path)


def _website(text):
    text = str(text or "").strip()
    if not text:
        return ""
    if not re.match(r"^https?://", text, re.I):
        text = "https://" + text.lstrip("/")
    return text if re.match(r"^https?://[^\s/]+\.[^\s/]+", text, re.I) else ""


def _link(text):
    """The text as a web address when it is one (a link or a bare domain such as jobs.lever.co/acme), else ""."""
    text = str(text or "").strip()
    if not text or re.search(r"\s", text):
        return ""
    if not re.match(r"^https?://", text, re.I) and not re.match(r"^(?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,}(?:[/?#].*)?$", text):
        return ""
    return _website(text)


def _point(place, address, home_state=""):
    """(lat, lon) for the place text (a ZIP code or "Town, ST"), else for a ZIP code inside the address, else None."""
    place = str(place or "").strip()
    if re.fullmatch(r"\d{5}(?:-\d{4})?", place):
        point = zip_point(place)
    else:
        point = place_point(place, home_state) if place else None
    if point:
        return point
    found = re.search(r"\b(\d{5})(?:-\d{4})?\b", str(address or ""))
    return zip_point(found.group(1)) if found else None


def clean(fields, home_state=""):
    """The stored form of what was typed, or raises ValueError with a message for the page."""
    values = {key: " ".join(str(fields.get(key) or "").split())[:limit] for key, limit in FIELD_LIMITS.items() if key != "notes"}
    values["notes"] = str(fields.get("notes") or "").strip()[:FIELD_LIMITS["notes"]]
    parts = {key: " ".join(str(fields.get(key) or "").split()) for key in ADDRESS_PARTS}
    if any(parts.values()):  # the form sends the address in four boxes: it is kept as one line, and the town is the place
        values["address"] = ", ".join(text for text in (parts["street"], parts["city"], parts["state"], parts["zip"]) if text)[:FIELD_LIMITS["address"]]
        values["place"] = (f"{parts['city']}, {parts['state']}" if parts["city"] and parts["state"] else parts["zip"])[:FIELD_LIMITS["place"]]
    if not values["name"]:
        raise ValueError("Enter the company's name.")
    raw_site = values["website"]
    values["website"] = _website(raw_site)
    if raw_site and not values["website"]:
        raise ValueError("That website doesn't look right. Use an address like acme.com.")
    # The job board is whatever you know: a link to it, or just its name ("Indeed", "the company's own portal").
    values["jobs_board"] = " ".join(str(fields.get("jobs_board", fields.get("jobs_url")) or "").split())[:FIELD_LIMITS["jobs_board"]]
    point = _point(values["place"], values["address"], home_state)
    if values["place"] and not point and not re.search(r"\d{5}", values["address"]):
        raise ValueError("That town wasn't found. Use a town and state such as Bel Air, MD, or a 5-digit ZIP code, "
                         "or leave it empty.")
    values["lat"], values["lon"] = (round(point[0], 6), round(point[1], 6)) if point else (None, None)
    return values


def add(fields, home_state=""):
    companies = load()
    if len(companies) >= MAX_COMPANIES:
        raise ValueError(f"The list is full ({MAX_COMPANIES} companies). Delete some first.")
    entry = dict(clean(fields, home_state), id=uuid.uuid4().hex[:12],
                 added=datetime.now().astimezone().isoformat(timespec="seconds"))
    _save(companies + [entry])
    return entry


def get(company_id):
    """The stored company with this id; KeyError when there is none."""
    for item in load():
        if item["id"] == company_id:
            return item
    raise KeyError(company_id)


def save_all(companies):
    """Replace the whole list (employer_sync.py uses it to refresh several companies at once)."""
    _save(companies)


def website_address(text):
    """The text as a website address (https://acme.com), or "" when it is not one."""
    return _website(text)


def same_company(first, second):
    """True when two names are the same company (ignoring case, punctuation and an Inc / LLC ending)."""
    key = _same(first)
    return bool(key) and key == _same(second)


def update(company_id, fields, home_state=""):
    companies = load()
    for index, item in enumerate(companies):
        if item["id"] == company_id:
            companies[index] = dict(item, **clean(fields, home_state))
            _save(companies)
            return companies[index]
    raise KeyError(company_id)


def delete(company_id):
    companies = load()
    kept = [item for item in companies if item["id"] != company_id]
    if len(kept) == len(companies):
        raise KeyError(company_id)
    _save(kept)
    gone = next(item for item in companies if item["id"] == company_id)
    if gone.get("source") == "resume":
        _remember_removed(gone["name"])


def _same(name):
    """A company name as compared: lower case, letters and digits only, without the Inc / LLC ending."""
    text = re.sub(r"\b(?:inc|incorporated|llc|ltd|corp|corporation|co|company)\b\.?", " ", str(name or "").casefold())
    return re.sub(r"[^a-z0-9]+", "", text)


def _removed():
    try:
        data = json.loads(paths.MY_COMPANIES_REMOVED_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    return {str(item) for item in data} if isinstance(data, list) else set()


def _remember_removed(name):
    removed = _removed() | {_same(name)}
    path = paths.MY_COMPANIES_REMOVED_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(sorted(removed)), encoding="utf-8")


def sync_from_work_history(work_history, home_state=""):
    """Add the employers in a work history that are not in the list yet. Returns how many were added.

    Each is added once with what the work history knows (town, ZIP, phone, website), a note saying where it came from,
    and source "resume". A company you already have (by name, ignoring Inc / LLC) or one you deleted is left alone."""
    companies = load()
    have = {_same(item["name"]) for item in companies} | _removed()
    added = []
    for job in work_history or []:
        if not isinstance(job, dict):
            continue
        name = " ".join(str(job.get("company") or "").split())[:FIELD_LIMITS["name"]]
        key = _same(name)
        if not name or not key or key in have:
            continue
        have.add(key)
        town = ", ".join(part for part in (str(job.get("city") or "").strip(), str(job.get("state") or "").strip()) if part)
        zip_code = str(job.get("zip") or "").strip()
        place = town if town and "," in town else (zip_code or "")
        street = str(job.get("street") or "").strip()
        address = ", ".join(part for part in (street, town, zip_code) if part)
        fields = {"name": name, "website": job.get("website") or "", "address": address, "place": place,
                  "phone": job.get("phone") or "",
                  "notes": "From your résumé: " + ", ".join(
                      part for part in (str(job.get("role") or "").strip(), str(job.get("dates") or "").strip()) if part)}
        try:
            entry = clean(fields, home_state)
        except ValueError:  # a place or website that can't be read is left out, the company is still worth listing
            entry = clean({"name": name, "notes": fields["notes"]}, home_state)
        added.append(dict(entry, id=uuid.uuid4().hex[:12], source="resume",
                          added=datetime.now().astimezone().isoformat(timespec="seconds")))
    if added:
        _save((companies + added)[:MAX_COMPANIES])
    return len(added)


def as_result(item, center=None):
    """An entered company in the shape the Companies page lists: miles from center, or None when it has no map point."""
    miles = None
    if center is not None and item.get("lat") is not None and item.get("lon") is not None:
        miles = round(miles_between(center, (float(item["lat"]), float(item["lon"]))), 1)
    from jobfinder.web.employer_sync import split_address  # here, not at the top: employer_sync imports this module
    jobs_board = str(item.get("jobs_board") or item.get("jobs_url") or "")
    jobs_url = _link(jobs_board)
    board = (identify(jobs_url) or {}).get("system", "") if jobs_url else ""
    return {"id": f"mine/{item['id']}", "mine": True, "name": item["name"], "type": "My company", "address": item.get("address", ""),
            "place": item.get("place", ""), "website": item.get("website", ""), "jobs_board": jobs_board,
            "jobs_url": jobs_url, "jobs_system": board.title() if board else "", "phone": item.get("phone", ""),
            "notes": item.get("notes", ""), **split_address(item.get("address"), item.get("place")), "lat": item.get("lat"), "lon": item.get("lon"), "miles": miles}


def everything(center=None):
    """All of your companies as the page lists them: nearest first when a center is given, then the ones that cannot be
    measured. Unlike near, none is left out for being far away."""
    results = [as_result(item, center) for item in load()]
    measured = sorted((r for r in results if r["miles"] is not None), key=lambda r: (r["miles"], r["name"].casefold()))
    unmeasured = sorted((r for r in results if r["miles"] is None), key=lambda r: r["name"].casefold())
    return measured + unmeasured


def near(lat, lon, miles):
    """Entered companies within miles of a point, nearest first, then the ones with no map point (they cannot be measured
    but are yours, so they are always listed)."""
    center = (float(lat), float(lon))
    results = [as_result(item, center) for item in load()]
    measured = sorted((r for r in results if r["miles"] is not None and r["miles"] <= float(miles)),
                      key=lambda r: (r["miles"], r["name"].casefold()))
    unmeasured = sorted((r for r in results if r["miles"] is None), key=lambda r: r["name"].casefold())
    return measured + unmeasured
