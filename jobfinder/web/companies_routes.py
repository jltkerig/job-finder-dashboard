"""The Companies page: businesses, offices, plants and schools near a place, with their address and website."""

import re

from flask import render_template, request, jsonify

from jobfinder.profiles.travel import place_point, zip_point
from jobfinder.sources import local_companies
from jobfinder.web import listing_queries, profile_store
from jobfinder.web.core import api_error, app

MILE_CHOICES = (5, 10, 15, 25, 50)
KNOWN = "known"  # employers from your own searches: answered from the database, with no network
KNOWN_LABEL = "Employers from your searches"


def _saved_places(profile):
    """The places you search around (your saved cities, then your home ZIP), for the quick buttons on the page."""
    places = [str(item.get("city") or "").strip() for item in profile.get("cities") or [] if isinstance(item, dict)]
    places = [place for place in places if place]
    if profile.get("home_zip"):
        places.append(str(profile["home_zip"]))
    return list(dict.fromkeys(places))


def resolve_place(text, profile):
    """((lat, lon), the name to show) for a ZIP or "Town, ST"; blank uses your first saved place. None when unknown."""
    text = str(text or "").strip()
    if not text:
        saved = _saved_places(profile)
        if not saved:
            return None
        text = saved[0]
    state = str(profile.get("state") or "")
    if re.fullmatch(r"\d{5}(?:-\d{4})?", text):
        point = zip_point(text)
    else:
        point = place_point(text, state)
    return (point, text) if point else None


@app.route("/companies")
def companies_page():
    profile = profile_store.get_user_profile()
    return render_template("companies.html", saved_places=_saved_places(profile), mile_choices=MILE_CHOICES,
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
        else:
            companies = local_companies.search(lat, lon, miles, kinds or local_companies.DEFAULT_KINDS)
    except ValueError as error:
        return api_error("E3503", str(error), 502)
    return jsonify({"status": "ok", "place": shown, "center": {"lat": lat, "lon": lon}, "miles": miles,
                    "count": len(companies), "companies": companies})
