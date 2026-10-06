"""Top 10 Picks routes: read each listing's requirements, preview a posting, rate a pick."""

from datetime import date

from flask import jsonify, request
from mysql.connector import Error

from jobfinder import db
from jobfinder.web.core import api_error, app


MIN_POSTING_TEXT = 300  # less than this is a page shell (menus, "Privacy Policy"), not the posting


def posting_urls(url):
    """Where the posting's text is: iCIMS shows it inside a frame, so its frame address is read first."""
    if not url:
        return []
    if "icims.com" in url and "in_iframe=" not in url:
        return [url + ("&" if "?" in url else "?") + "in_iframe=1", url]
    return [url]


def _requirements_for(row, cursor, connection):
    """The listing's requirements, read once from its saved description or page and kept in listing_details; None
    when the page can't be read."""
    import json

    from jobfinder.profiles import requirements as reqs
    import re

    from jobfinder.profiles.profile_tools import detect_skills, normalize_skills
    from jobfinder.search.relevance import fetch_text
    from jobfinder.web.profile_store import get_user_profile

    try:
        details = json.loads(row.get("listing_details") or "{}") or {}
    except (TypeError, ValueError):
        details = {}
    found = details.get("requirements")
    if found is not None and found.get("version") == reqs.VERSION:
        return found
    try:
        skills = json.loads(row.get("listing_skills") or "[]") or []
    except (TypeError, ValueError):
        skills = []
    text = details.get("description") or ""
    today = date.today().isoformat()
    unread_key = f"unread_on_v{reqs.VERSION}"
    if len(text) < MIN_POSTING_TEXT and details.get(unread_key) == today:
        return None  # already failed today: don't fetch it again on every Top 10
    if len(text) < MIN_POSTING_TEXT:
        text = ""
        for url in [link for base in (row.get("career_url"), row.get("source_url")) for link in posting_urls(base)]:
            page = fetch_text(url)
            body = reqs.page_text(page.text) if page and page.text else ""
            if len(body) >= MIN_POSTING_TEXT:
                text = body
                break
    if not text:
        details[unread_key] = today
        cursor.execute("UPDATE companies SET listing_details = %s WHERE id = %s", (json.dumps(details), row["id"]))
        connection.commit()
        return None
    found = reqs.listing_requirements(text)
    # Skills from the posting text too: many job-board listings were saved without any, so their fit was unknown.
    # Your own skills are looked for by name as well, since not all of them are in the built-in skills list.
    own = (get_user_profile() or {}).get("skills") or []
    named = [skill for skill in own if re.search(r"(?<![\w])" + re.escape(skill) + r"(?![\w])", text, re.I)]
    all_skills = normalize_skills(list(skills) + detect_skills(text) + named)
    found["skill_sections"] = reqs.skill_sections(text, all_skills)
    details["requirements"] = found
    cursor.execute("UPDATE companies SET listing_details = %s, listing_skills = %s WHERE id = %s",
                   (json.dumps(details), json.dumps(all_skills), row["id"]))
    connection.commit()
    return found


LISTING_COLUMNS = "id, career_url, source_url, listing_details, listing_skills"


@app.route("/top-picks/requirements", methods=["POST"])
def top_pick_requirements():
    """What each listing asks for that the profile doesn't show (degrees, years, clearance), for Top 10 Picks."""
    from jobfinder.profiles import requirements as reqs
    from jobfinder.web.profile_store import get_user_profile

    data = request.get_json(silent=True) or {}
    ids = []
    for company_id in (data.get("company_ids") or [])[:50]:
        try:
            ids.append(int(company_id))
        except (TypeError, ValueError):
            continue
    if not ids:
        return jsonify({"status": "ok", "gaps": {}})
    profile = get_user_profile() or {}
    gaps = {}
    connection = cursor = None
    try:
        connection = db.connect()
        cursor = connection.cursor(dictionary=True)
        cursor.execute(f"SELECT {LISTING_COLUMNS} FROM companies WHERE id IN ({','.join(['%s'] * len(ids))})", ids)
        for row in cursor.fetchall():
            found = _requirements_for(row, cursor, connection)
            if found is None:
                gaps[row["id"]] = {"unread": True, "items": []}
                continue
            gaps[row["id"]] = {"unread": False, "items": reqs.requirement_gaps(found, profile),
                               "fit": reqs.weighted_fit(profile.get("skills") or [], found.get("skill_sections"))}
    except Error as error:
        return api_error("E3230", f"Could not read listing requirements: {error}", 500)
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()
    return jsonify({"status": "ok", "gaps": gaps})


@app.route("/listing-preview/<int:company_id>")
def listing_preview(company_id):
    """Some of the job posting for the Details panel: warnings, the requirement / responsibility lines, and the text."""
    from jobfinder.profiles import requirements as reqs
    from jobfinder.web.profile_store import get_user_profile

    connection = cursor = None
    try:
        connection = db.connect()
        cursor = connection.cursor(dictionary=True)
        cursor.execute(f"SELECT {LISTING_COLUMNS} FROM companies WHERE id = %s", (company_id,))
        row = cursor.fetchone()
        if not row:
            return api_error("E3231", "The listing could not be found.", 404)
        found = _requirements_for(row, cursor, connection)
    except Error as error:
        return api_error("E3231", f"Could not read the job posting: {error}", 500)
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()
    if found is None:
        return jsonify({"status": "ok", "unread": True})
    text = found.get("text") or ""
    return jsonify({"status": "ok", "unread": False, "gaps": reqs.requirement_gaps(found, get_user_profile() or {}),
                    "highlights": reqs.key_lines(text), "text": reqs.posting_body(text)[:12000]})


@app.route("/top-picks/rate", methods=["POST"])
def rate_top_pick():
    """👍 / 👎 on a Top 10 pick: kept in listing_details so later picks favour or mark down listings like it. "none"
    clears it."""
    import json

    data = request.get_json(silent=True) or {}
    rating = data.get("rating")
    try:
        company_id = int(data.get("company_id"))
    except (TypeError, ValueError):
        return jsonify({"status": "error", "message": "Invalid listing."}), 400
    if rating not in {"up", "down", "none"}:
        return jsonify({"status": "error", "message": "Invalid rating."}), 400
    connection = cursor = None
    try:
        connection = db.connect()
        cursor = connection.cursor(dictionary=True)
        cursor.execute("SELECT listing_details FROM companies WHERE id = %s", (company_id,))
        row = cursor.fetchone()
        if not row:
            return api_error("E3232", "The listing could not be found.", 404)
        try:
            details = json.loads(row.get("listing_details") or "{}") or {}
        except (TypeError, ValueError):
            details = {}
        if rating == "none":
            details.pop("pick_rating", None)
        else:
            details["pick_rating"] = rating
        cursor.execute("UPDATE companies SET listing_details = %s WHERE id = %s", (json.dumps(details), company_id))
        connection.commit()
    except Error as error:
        return api_error("E3232", f"Could not save the rating: {error}", 500)
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()
    return jsonify({"status": "ok", "rating": rating})
