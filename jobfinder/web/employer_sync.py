"""Keeps the Companies page and your profile's work history in step.

An employer on the Companies page that is also a job in your work history shares its address, phone number and website
with that job. What you save on the Companies page is written to the job, so the Dashboard's Profile and the Résumé
Builder show it too (they read the same work history). What a job in your Profile has, typed there or read from your
résumé, is shown on the Companies page for the employers that came from your résumé.

Nothing is wiped by accident: adding a company only fills details the job has no text for, and editing one writes just
the boxes you changed (a box you empty is emptied on the job too).
"""

import re

from jobfinder.profiles.places import STATE_NAMES
from jobfinder.web import my_companies, profile_store

ADDRESS_PARTS = ("street", "city", "state", "zip")
SHARED = ADDRESS_PARTS + ("phone", "website")
STATE_BY_NAME = {name.casefold(): code for code, name in STATE_NAMES.items()}
_ZIP_END = re.compile(r"^(?:(?P<state>[A-Za-z][A-Za-z .]*?)\s+)?(?P<zip>\d{5})(?:-\d{4})?$")
_UNIT = re.compile(r"^(?:suite|ste\.?|apt\.?|unit|floor|fl\.?|room|rm\.?|bldg\.?|building)\b|^#", re.I)


def _state_code(text):
    text = str(text or "").strip().rstrip(".")
    return text.upper() if text.upper() in STATE_NAMES else STATE_BY_NAME.get(text.casefold(), "")


def split_address(address, place=""):
    """The address as the profile keeps it: {"street", "city", "state", "zip"}, "" for a part that is not there.

    Reads "757 Frederick Rd Suite 301, Baltimore, MD, 21228", "100 Main St, Bel Air, MD 21014", "Bel Air, MD" and the
    like. A town, state or ZIP code missing from the address is taken from `place` (the town or ZIP code typed to
    measure distance from)."""
    parts = [piece.strip() for piece in str(address or "").replace("\n", ",").split(",") if piece.strip()]
    state = zip_code = city = ""
    if parts:
        match = _ZIP_END.match(parts[-1])
        if match and (not match.group("state") or _state_code(match.group("state"))):
            zip_code = match.group("zip")
            state = _state_code(match.group("state"))
            parts.pop()
    if parts and not state and _state_code(parts[-1]):
        state = _state_code(parts.pop())
    last_is_town = bool(parts) and not _UNIT.match(parts[-1]) and not re.fullmatch(r"[\d\s#-]+", parts[-1])
    if last_is_town and (len(parts) >= 2 or ((state or zip_code) and not re.search(r"\d", parts[-1]))):
        city = parts.pop()
    place = str(place or "").strip()
    if place:
        if re.fullmatch(r"\d{5}(?:-\d{4})?", place):
            zip_code = zip_code or place[:5]
        elif "," in place:
            town, _, region = place.rpartition(",")
            city = city or town.strip()
            state = state or _state_code(region)
    return {"street": ", ".join(parts), "city": city, "state": state, "zip": zip_code}


def join_address(parts):
    """The parts as one line: "street, city, state, ZIP" (what the Companies page shows)."""
    return ", ".join(text for text in (parts.get("street"), parts.get("city"), parts.get("state"), parts.get("zip")) if text)


def _place(parts):
    """The "Town, ST" (else the ZIP code) the Companies page measures distance from."""
    if parts.get("city") and parts.get("state"):
        return f"{parts['city']}, {parts['state']}"
    return parts.get("zip", "")


def details_from_entry(entry):
    """What a company entry says about the shared details: street, city, state, zip, phone and website."""
    shared = split_address(entry.get("address"), entry.get("place"))
    shared["phone"] = str(entry.get("phone") or "").strip()
    shared["website"] = str(entry.get("website") or "").strip()
    return shared


def _same_text(key, a, b):
    """Two values of one detail as the same text, ignoring case, spacing, a www. and a closing slash on websites, and the
    punctuation in a phone number."""
    if key == "phone":
        return re.sub(r"\D", "", str(a or "")) == re.sub(r"\D", "", str(b or ""))
    if key == "website":
        def bare(text):
            return re.sub(r"^https?://(?:www\.)?", "", str(text or "").strip().casefold()).rstrip("/")
        return bare(a) == bare(b)
    return " ".join(str(a or "").casefold().split()) == " ".join(str(b or "").casefold().split())


def push(entry, before=None):
    """Write what a company entry says about its address, phone and website to the matching jobs in the work history.

    before is the entry as it was before an edit: only the boxes that changed are written, and an address moves as a
    whole. With no before (a company just added) only details the job has no text for are filled in. Returns how many
    jobs matched (0 when none did or nothing changed), or None when the Profile could not be changed."""
    new = details_from_entry(entry)
    if before is None:
        values, fill_only = {key: text for key, text in new.items() if text}, True
    else:
        old = details_from_entry(before)
        values, fill_only = {key: text for key, text in new.items() if not _same_text(key, text, old[key])}, False
        if any(key in values for key in ADDRESS_PARTS):
            values.update({key: new[key] for key in ADDRESS_PARTS})
    if not values:
        return 0
    name = (before or entry)["name"]  # a job keeps the name it had, even after the company is renamed here
    return profile_store.update_work_details(lambda company: my_companies.same_company(company, name), values, fill_only)


def refresh_from_work_history(work_history, home_state=""):
    """Show on the Companies page what the work history has for the employers that came from your résumé.

    Only details the job has text for are taken (a job with an empty box never empties a company), and an address is
    merged part by part. Returns how many companies changed."""
    jobs = [job for job in work_history or [] if isinstance(job, dict) and str(job.get("company") or "").strip()]
    companies = my_companies.load()
    changed = 0
    for index, item in enumerate(companies):
        if item.get("source") != "resume":
            continue
        theirs = next(({key: str(job.get(key) or "").strip() for key in SHARED} for job in jobs
                       if my_companies.same_company(job["company"], item["name"])
                       and any(str(job.get(key) or "").strip() for key in SHARED)), None)
        if theirs is None:
            continue
        mine = details_from_entry(item)
        changes = {}
        merged = {key: theirs[key] or mine[key] for key in ADDRESS_PARTS}
        if any(not _same_text(key, merged[key], mine[key]) for key in ADDRESS_PARTS):
            changes["address"] = join_address(merged)
            if not item.get("place"):
                changes["place"] = _place(merged)
        if theirs["phone"] and not _same_text("phone", theirs["phone"], mine["phone"]):
            changes["phone"] = theirs["phone"]
        site = my_companies.website_address(theirs["website"])
        if site and not _same_text("website", site, mine["website"]):
            changes["website"] = site
        if not changes:
            continue
        try:
            companies[index] = dict(item, **my_companies.clean({**{key: item.get(key, "") for key in my_companies.FIELD_LIMITS}, **changes}, home_state))
        except ValueError:  # a detail that cannot be read leaves the company as it was
            continue
        changed += 1
    if changed:
        my_companies.save_all(companies)
    return changed
