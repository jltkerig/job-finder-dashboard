"""The Companies page: businesses, offices, plants and schools near a place, with their address and website."""

import re

from flask import render_template, request, jsonify

from jobfinder.profiles.travel import place_point, zip_point
from jobfinder.sources import local_companies
from jobfinder.web import employer_sync, listing_queries, my_companies, profile_store
from jobfinder.web.core import api_error, app

MILE_CHOICES = (5, 10, 15, 25, 50)
KNOWN = "known"  # employers from your own searches: answered from the database, with no network
KNOWN_LABEL = "Employers from your searches"
MINE = "mine"  # the companies you entered yourself (user-data/my_companies.json)
MINE_LABEL = "My companies"


def _saved_places(profile):
    """The places you search around (your saved cities, then your home ZIP), for the quick buttons on the page."""
    places = [str(item.get("city") or "").strip() for item in profile.get("cities") or [] if isinstance(item, dict)]
    places = [place for place in places if place]
    if profile.get("home_zip"):
        places.append(str(profile["home_zip"]))
    return list(dict.fromkeys(places))


def home_place(profile):
    """The one place the page searches around unless you type another: your home (the Dashboard's Home City, State, else
    the Home ZIP), else the first town you search jobs in. "" when the profile has none."""
    for text in (profile.get("home_location"), profile.get("home_zip")):
        if str(text or "").strip():
            return str(text).strip()
    saved = _saved_places(profile)
    return saved[0] if saved else ""


def resolve_place(text, profile):
    """((lat, lon), the name to show) for a ZIP or "Town, ST"; blank uses your home. None when unknown."""
    text = str(text or "").strip() or home_place(profile)
    if not text:
        return None
    state = str(profile.get("state") or "")
    if re.fullmatch(r"\d{5}(?:-\d{4})?", text):
        point = zip_point(text)
    else:
        point = place_point(text, state)
    return (point, text) if point else None


@app.route("/companies")
def companies_page():
    profile = profile_store.get_user_profile()
    try:  # the employers in your work history (filled from your résumé) join your companies, once each
        my_companies.sync_from_work_history(profile.get("work_history"), str(profile.get("state") or ""))
        employer_sync.refresh_from_work_history(profile.get("work_history"), str(profile.get("state") or ""))  # and show what the Profile has for them
    except OSError:
        pass  # the list is a convenience; the page still opens
    return render_template("companies.html", home_place=home_place(profile), mile_choices=MILE_CHOICES,
                           kinds=[(KNOWN, KNOWN_LABEL)] + [(key, label) for key, (label, _) in local_companies.KINDS.items()],
                           default_kinds=[KNOWN] + list(local_companies.DEFAULT_KINDS), default_miles=15)


@app.route("/companies/search")
def companies_search():
    profile = profile_store.get_user_profile()
    found = resolve_place(request.args.get("place"), profile)
    if found is None:
        if not (request.args.get("place") or "").strip():
            return api_error("E3501", "Type a town and state (Bel Air, MD) or a ZIP code to search around.", 400)
        return api_error("E3502", "That place wasn't found. Use a town and state such as Bel Air, MD, or a 5-digit ZIP code.", 404)
    (lat, lon), shown = found
    try:
        miles = float(request.args.get("miles") or 15)
    except ValueError:
        miles = 15.0
    miles = min(max(miles, 1.0), 50.0)
    asked = [key.strip() for key in (request.args.get("kinds") or "").split(",")]
    kinds = [key for key in asked if key in local_companies.KINDS]
    try:
        if asked == [KNOWN]:
            companies = listing_queries.get_known_employers(lat, lon, miles)
        elif asked == [MINE]:
            companies = my_companies.near(lat, lon, miles)
        else:
            companies = local_companies.search(lat, lon, miles, kinds or local_companies.DEFAULT_KINDS)
    except ValueError as error:
        return api_error("E3503", str(error), 502)
    return jsonify({"status": "ok", "place": shown, "center": {"lat": lat, "lon": lon}, "miles": miles,
                    "count": len(companies), "companies": companies})


def _fields_from_request():
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


@app.route("/companies/mine", methods=["GET"])
def my_company_list():
    """All of your own companies, nearest first from the place given (or your home); the page shows them in their own box."""
    profile = profile_store.get_user_profile()
    found = resolve_place(request.args.get("place"), profile)
    center, shown = (found[0], found[1]) if found else (None, "")
    companies = my_companies.everything(center)
    return jsonify({"status": "ok", "place": shown, "count": len(companies), "companies": companies})


@app.route("/companies/mine", methods=["POST"])
def my_company_add():
    """Add a company you entered yourself."""
    state = str(profile_store.get_user_profile().get("state") or "")
    try:
        entry = my_companies.add(_fields_from_request(), state)
    except ValueError as error:
        return api_error("E3511", str(error), 400)
    synced = employer_sync.push(entry)  # a job in your work history at this company gets its empty details filled in
    return jsonify({"status": "ok", "company": my_companies.as_result(entry), "jobs_synced": synced or 0,
                    "profile_synced": synced is not None})


@app.route("/companies/mine/<company_id>", methods=["POST"])
def my_company_update(company_id):
    state = str(profile_store.get_user_profile().get("state") or "")
    fields = _fields_from_request()
    try:
        before = my_companies.get(company_id)
        changed = dict(before, **my_companies.clean(fields, state))
    except ValueError as error:
        return api_error("E3511", str(error), 400)
    except KeyError:
        return api_error("E3512", "That company is no longer in your list.", 404)
    # The Profile first: an employer from your résumé is shown from the Profile again, so a change kept only here would
    # be undone the next time the page opens. A company you entered yourself is saved whatever happens to the Profile.
    synced = employer_sync.push(changed, before)
    if synced is None and before.get("source") == "resume":
        return api_error("E3513", "Couldn't update the job's details in your Profile, so nothing was saved. Try again.", 500)
    try:
        entry = my_companies.update(company_id, fields, state)
    except KeyError:
        return api_error("E3512", "That company is no longer in your list.", 404)
    return jsonify({"status": "ok", "company": my_companies.as_result(entry), "jobs_synced": synced or 0,
                    "profile_synced": synced is not None})


@app.route("/companies/mine/<company_id>/delete", methods=["POST"])
def my_company_delete(company_id):
    try:
        my_companies.delete(company_id)
    except KeyError:
        return api_error("E3512", "That company is no longer in your list.", 404)
    return jsonify({"status": "ok"})
