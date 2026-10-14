"""Top 10 Picks routes: read each listing's requirements, preview a posting, rate a pick."""

from datetime import date

from flask import jsonify, request
from mysql.connector import Error

from jobfinder import db
from jobfinder.sources.board_status import board_status
from jobfinder.sources.posting_text import posting_text
from jobfinder.web.core import api_error, app


MIN_POSTING_TEXT = 600  # less than this is a page shell (menus, "Privacy Policy"), not the posting


def posting_urls(url):
    """Where the posting's text is: iCIMS shows it inside a frame, so its frame address is read first."""
    if not url:
        return []
    if "icims.com" in url and "in_iframe=" not in url:
        return [url + ("&" if "?" in url else "?") + "in_iframe=1", url]
    return [url]


LIVE_CLOSED_CHECKS = 12  # the best picks get a live "still open?" look, once a day each


def closed_posting(row, found, cursor, connection, live):
    """True when the posting says it's closed ("No longer accepting applications"); the listing is then marked Closed.
    With live, the page is read again (once a day): LinkedIn jobs aren't rechecked by Refresh, and a description saved
    when the job was captured never shows that it closed later."""
    import json

    from jobfinder.profiles import requirements as reqs
    from jobfinder.search.company_check import CLOSED
    from jobfinder.search.relevance import fetch_text

    closed = bool(CLOSED.search(((found or {}).get("text") or "")[:20000]))
    cursor.execute("SELECT listing_details FROM companies WHERE id = %s", (row["id"],))
    latest = cursor.fetchone() or {}
    try:
        details = json.loads(latest.get("listing_details") or "{}") or {}
    except (TypeError, ValueError):
        details = {}
    today = date.today().isoformat()
    if not closed and live and board_status(row.get("career_url") or "") == "Closed":
        closed = True  # the employer's own hiring board (Ashby, Greenhouse, Lever ...) no longer lists it
    if not closed and live and details.get("closed_check") != today and (found or {}).get("read_on") != today:
        for url in posting_urls(row.get("career_url") or row.get("source_url")):
            body = reqs.page_text(posting_text(url))
            if len(body) < MIN_POSTING_TEXT:
                page = fetch_text(url)
                body = reqs.page_text(page.text) if page and page.text else ""
            if len(body) >= MIN_POSTING_TEXT:
                closed = bool(CLOSED.search(body[:20000]))
                break
        details["closed_check"] = today
        cursor.execute("UPDATE companies SET listing_details = %s WHERE id = %s", (json.dumps(details), row["id"]))
    if closed:
        cursor.execute("UPDATE companies SET job_open_status = 'Closed' WHERE id = %s", (row["id"],))
    connection.commit()
    return closed


def _requirements_for(row, cursor, connection, refresh=False):
    """The listing's requirements, read once from its saved description or page and kept in listing_details; None
    when the page can't be read. With refresh, the live page is read again whatever was kept (the saved description
    is the fallback when the page can't be read)."""
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
    if not refresh and found is not None and found.get("version") == reqs.VERSION:
        return found
    try:
        skills = json.loads(row.get("listing_skills") or "[]") or []
    except (TypeError, ValueError):
        skills = []
    text = details.get("description") or ""
    stored = text if refresh else ""
    if refresh:
        text = ""
    today = date.today().isoformat()
    unread_key = f"unread_on_v{reqs.VERSION}"
    if not refresh and len(text) < MIN_POSTING_TEXT and details.get(unread_key) == today:
        return None  # already failed today: don't fetch it again on every Top 10
    live_read = False
    if len(text) < MIN_POSTING_TEXT:
        text = ""
        for url in [link for base in (row.get("career_url"), row.get("source_url")) for link in posting_urls(base)]:
            body = reqs.page_text(posting_text(url))  # Workday, Oracle, NLX: from their data service
            if len(body) < MIN_POSTING_TEXT:
                page = fetch_text(url)
                body = reqs.page_text(page.text) if page and page.text else ""
            if len(body) >= MIN_POSTING_TEXT:
                text = body
                live_read = True
                break
        if not text and len(stored) >= MIN_POSTING_TEXT:
            text = stored
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
    found["read_on"] = today if live_read or not details.get("description") else ""  # a live page, read today
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
    refresh = bool(data.get("refresh"))  # one pick's Refresh button: read its page again
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
        rows = {row["id"]: row for row in cursor.fetchall()}
        for rank, company_id in enumerate(company_id for company_id in ids if company_id in rows):
            row = rows[company_id]
            found = _requirements_for(row, cursor, connection, refresh=refresh)
            if closed_posting(row, found, cursor, connection, live=rank < LIVE_CLOSED_CHECKS):
                gaps[row["id"]] = {"closed": True, "unread": False, "items": []}
                continue
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
    profile = get_user_profile() or {}
    mine = {str(skill).casefold() for skill in profile.get("skills") or []}
    order = {"required": 0, "preferred": 1}  # then the ones only mentioned
    skills = sorted(({"name": name, "where": where, "have": name.casefold() in mine}
                     for name, where in (found.get("skill_sections") or {}).items()),
                    key=lambda skill: order.get(skill["where"], 2))
    return jsonify({"status": "ok", "unread": False, "gaps": reqs.requirement_gaps(found, profile),
                    "highlights": reqs.key_lines(text), "skills": skills, "text": reqs.posting_body(text)[:12000]})
