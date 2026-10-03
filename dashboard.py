from datetime import datetime, timedelta, timezone
import gzip
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import threading
import secrets
import re
import json
import shutil
import tempfile
import time
from urllib.parse import quote, urlparse
from zoneinfo import ZoneInfo

import mysql.connector
from jobfinder import db, paths
from dotenv import load_dotenv
from flask import Flask, abort, jsonify, redirect, render_template, request, send_from_directory, session
from mysql.connector import Error
from jobfinder.profiles.profile_tools import (AMBIGUOUS_SKILLS, SKILL_ALIASES, fit_score, normalize_skills, resume_skill_suggestions,
                           parse_work_history, refresh_listing_skills, resume_suggestions, skill_demand,
                           uploaded_resume)
from jobfinder.profiles.onet_data import occupation_skill_suggestions, proper_title, related_title_suggestions, spelling_fix, title_matches
from jobfinder.profiles.places import city_matches
from jobfinder.profiles.travel import describe as describe_trip
from jobfinder.sources.job_listings import NON_JOB_PATH, is_pdf_url
from jobfinder.db_schema import ensure_unique_source_index
from jobfinder.sources.job_feeds import FEED_NAMES
from jobfinder.sources.job_sites import JOB_SITE_NAMES
from jobfinder.records.board_health import STATUSES as HEALTH_STATUSES, read_health
from jobfinder.records.search_skips import TTL_HOURS, latest_decisions
from jobfinder.records.capture_import import CAPTURE_SOURCES, capture_dirs, move_pending, pending_files
from jobfinder.records.job_retention import CLOSED_KEEP_DAYS, SAVED_STATUSES, tidy_closed_jobs
from jobfinder.web.webfiles import (  # noqa: F401  (also used by callers that import these from here)
    APPLICATION_STATUSES,
    BASE_DIR,
    BLOCKED_COMPANIES_FILE,
    BLOCKED_DOMAINS_FILE,
    BLOCK_METADATA_FILE,
    COMPRESSIBLE,
    EXTENSION_FILE,
    EXTENSION_ID,
    GRACEFUL_STOP_SECONDS,
    JOB_FINDER_PATH,
    LOCAL_HOSTS,
    LOCAL_TIMEZONE,
    RECOMMENDED_DOMAINS_FILE,
    RESUME_FOLDER,
    SEARCH_SKIPS_FILE,
    SKIPS_PER_PAGE,
    UPDATE_MARKER,
    UPDATE_SCRIPT,
    UPDATE_SEARCH_DIRS,
    UPDATE_ZIP_PATTERN,
)
from jobfinder.web import webfiles
from jobfinder.web.core import (  # noqa: F401  (also used by callers that import these from here)
    API_PATH_CODES,
    ERROR_CODES,
    api_error,
    app,
    attach_error_codes,
    get_csrf_token,
    handle_forbidden,
    handle_internal_error,
    how_long,
    inject_csrf_token,
    local_time,
    log_error_code,
    make_responses_lighter,
    protect_local_post_requests,
    refuse_foreign_hosts,
)
from jobfinder.web.schema import (  # noqa: F401  (also used by callers that import these from here)
    _safe_db_identifier,
    _schema_ready,
    ensure_keep_column,
    ensure_profile_tables,
    initialize_database,
    tidy_expired_closed_jobs,
)
from jobfinder.web import schema
from jobfinder.web.search_history import (  # noqa: F401  (also used by callers that import these from here)
    collapse_search_history,
    get_search_history,
    record_search_history,
    split_search_titles,
)
from jobfinder.web.profile_store import (  # noqa: F401  (also used by callers that import these from here)
    RELATED_JOB_TITLES,
    listing_skill_demand,
    profile_skill_suggestions,
    refresh_job_fit,
    related_job_title_suggestions,
)
from jobfinder.web import profile_store
from jobfinder.web.blocklists import (  # noqa: F401  (also used by callers that import these from here)
    add_blocked_company,
    add_domain_to_blocklist,
    block_company,
    block_details,
    manage_blocked_company,
    manage_blocked_domain,
    read_block_metadata,
    remove_domain_from_blocklist,
    save_block_metadata,
)
from jobfinder.web import blocklists


APP_VERSION = "1.1.150"

app.config.update(APP_VERSION=APP_VERSION)


scraper_process = None
scraper_mode = None
scraper_last_error = None
scraper_lock = threading.Lock()
scraper_started_at = None
scraper_stopping = False


def get_dashboard_counts():
    schema.ensure_job_tracking_columns()
    connection = None
    cursor = None
    counts = {"saved": 0, "applied": 0, "recruiter": 0, "interview": 0, "closed": 0}
    try:
        connection = db.connect()
        cursor = connection.cursor(dictionary=True)
        cursor.execute("""
            SELECT
                COUNT(*) AS saved,
                SUM(application_status = 'Applied') AS applied,
                SUM(application_status = 'Talking With Recruiter') AS recruiter,
                SUM(application_status = 'Interview') AS interview,
                SUM(job_open_status = 'Closed') AS closed
            FROM companies
            WHERE is_kept = 1 AND is_rejected = 0
        """)
        row = cursor.fetchone() or {}
        for key in counts:
            counts[key] = int(row.get(key) or 0)
        return counts
    except Error as error:
        print("Could not read dashboard counts.")
        print(error)
        return counts
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None and connection.is_connected():
            connection.close()


def get_rejected_companies():
    schema.ensure_job_tracking_columns()
    connection = None
    cursor = None
    try:
        connection = db.connect()
        cursor = connection.cursor(dictionary=True)
        cursor.execute("""
            SELECT id, name, career_job_title, career_credibility, domain, career_url,
                   source_url, source_type, country, state, city, latitude, longitude, distance_miles, usa_credibility, work_arrangement, date_found,
                   last_checked, result_updated_at, rejected_at, rejection_reason
            FROM companies
            WHERE is_rejected = 1
            ORDER BY rejected_at DESC, date_found DESC
        """)
        return cursor.fetchall()
    except Error as error:
        print("Could not read rejected listings.")
        print(error)
        return []
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None and connection.is_connected():
            connection.close()


def get_kept_companies(status_filter="", state_filter="", title_filter="", sort_by="date_desc"):
    schema.ensure_job_tracking_columns()
    connection = None
    cursor = None

    try:
        connection = db.connect()
        cursor = connection.cursor(dictionary=True)

        where = ["is_kept = 1", "is_rejected = 0"]
        params = []
        if status_filter:
            where.append("application_status = %s")
            params.append(status_filter)
        if state_filter:
            where.append("state LIKE %s")
            params.append(f"%{state_filter}%")
        if title_filter:
            where.append("career_job_title LIKE %s")
            params.append(f"%{title_filter}%")

        sort_map = {
            "date_asc": "date_found ASC",
            "company": "name ASC",
            "title": "career_job_title ASC",
            "status": "application_status ASC, date_found DESC",
            "verified": "last_checked DESC",
        }
        order_by = sort_map.get(sort_by, "date_found DESC")

        sql = f"""
            SELECT id, name, career_job_title, career_credibility, domain, career_url,
                   source_url, source_type, country, state, city, latitude, longitude, distance_miles, usa_credibility, work_arrangement, date_found,
                   last_checked, result_updated_at, is_kept, job_open_status, application_status, notes, listing_skills,
                   is_rejected, rejected_at
            FROM companies
            WHERE {' AND '.join(where)}
            ORDER BY {order_by}
        """
        cursor.execute(sql, params)
        return cursor.fetchall()
    except Error as error:
        print()
        print("Could not read kept companies from the database.")
        print(error)
        return []
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None and connection.is_connected():
            connection.close()


def add_drive_times(companies, home_zip, home_state=""):
    """company["drive"] = {"miles", "minutes", "text"} from the home ZIP, estimated on this computer (travel.py)."""
    for company in companies:
        point = None
        try:
            if company.get("latitude") is not None and company.get("longitude") is not None:
                point = (float(company["latitude"]), float(company["longitude"]))
        except (TypeError, ValueError):
            point = None
        place = ", ".join(part for part in (company.get("city"), company.get("state")) if part)
        company["drive"] = describe_trip(home_zip, point, place, home_state) if home_zip else None


def add_job_fit(companies, skills):
    for company in companies:
        try:
            found = json.loads(company.get("listing_skills") or "[]")
        except (TypeError, ValueError):
            found = []
        company["job_fit"] = fit_score(skills, found if isinstance(found, list) else [])


def get_companies():
    connection = None
    cursor = None

    try:
        connection = db.connect()

        cursor = connection.cursor(dictionary=True)

        cursor.execute("""
            SELECT
                id,
                name,
                career_job_title,
                career_credibility,
                domain,
                career_url,
                source_url,
                country,
                state,
                city,
                latitude,
                longitude,
                distance_miles,
                usa_credibility,
                work_arrangement,
                date_found,
                last_checked,
                result_updated_at,
                is_kept,
                job_open_status,
                application_status,
                notes,
                listing_skills,
                listing_details,
                source_type
            FROM companies
            WHERE is_rejected = 0
            ORDER BY date_found DESC
        """)

        rows = cursor.fetchall()
        blocked_domains = set(blocklists.get_blocked_domains())
        blocked_names = {name.casefold() for name in blocklists.get_blocked_companies()}
        # A job found to be closed disappears from the results (and is deleted after CLOSED_KEEP_DAYS); saved ones stay.
        rows = [row for row in rows if not (row.get("job_open_status") == "Closed" and not row.get("is_kept")
                                           and row.get("application_status") not in SAVED_STATUSES)]
        visible_rows = [row for row in rows if (row.get("name") or "").casefold() not in blocked_names
                and (row.get("is_kept") or not any(
                    NON_JOB_PATH.search(urlparse(row.get(key) or "").path)
                    or (urlparse(row.get(key) or "").hostname or "").startswith("catalystmag.")
                    for key in ("source_url", "career_url")))
                and not (re.search(r"/types-of-aid/employment/campus/?(?:[?#]|$)",
                                   (row.get("career_url") or "").lower())
                         and (row.get("career_job_title") or "").strip().casefold() in
                         {"directory search", "campus employment & internships", "student employment"})
                # The blocked-domain list is for web-search results; jobs you captured on those sites still show.
                and (row.get("source_type") in CAPTURE_SOURCES
                     or not any((row.get("domain") or "").lower().removeprefix("www.") == domain
                                or (row.get("domain") or "").lower().endswith("." + domain)
                                for domain in blocked_domains))]
        for row in visible_rows:
            try:
                row["details"] = json.loads(row.get("listing_details") or "{}")
            except (TypeError, ValueError):
                row["details"] = {}
            if not isinstance(row["details"], dict):
                row["details"] = {}
            row["source_host"] = (urlparse(row.get("source_url") or "").hostname or "").removeprefix("www.")
        return visible_rows

    except Error as error:
        print()
        print("Could not read companies from the database.")
        print(error)
        return []

    finally:
        if cursor is not None:
            cursor.close()

        if connection is not None and connection.is_connected():
            connection.close()


def scraper_status():
    global scraper_process, scraper_mode, scraper_last_error, scraper_stopping

    with scraper_lock:
        if scraper_process is None:
            return False, None

        exit_code = scraper_process.poll()
        if exit_code is None:
            return True, scraper_mode

        was_stopping = scraper_stopping
        scraper_stopping = False
        if exit_code != 0 and not was_stopping:
            scraper_last_error = (
                f"Job Finder stopped with exit code {exit_code}. "
                f"Details were saved to {webfiles.SCRAPER_LOG_FILE.name}."
            )

        scraper_process = None
        scraper_mode = None
        return False, None


def scraper_is_running():
    running, _ = scraper_status()
    return running


def find_latest_update_zip():
    candidates = []

    for folder in UPDATE_SEARCH_DIRS:
        if not folder.exists() or not folder.is_dir():
            continue

        try:
            entries = folder.iterdir()
        except OSError:
            continue

        for path in entries:
            if not path.is_file():
                continue

            match = UPDATE_ZIP_PATTERN.match(path.name)
            if not match:
                continue

            version = tuple(int(part) for part in match.groups())
            candidates.append((version, path))

    if not candidates:
        return None, None

    version, path = max(candidates, key=lambda item: item[0])
    return version, path


@app.route("/check-update")
def check_update():
    try:
        current_version = tuple(int(part) for part in app.config["APP_VERSION"].split("."))
        latest_version, latest_path = find_latest_update_zip()

        if latest_version is None:
            return jsonify({
                "status": "none_found",
                "current_version": app.config["APP_VERSION"],
                "message": "No Job Finder update ZIPs were found in Downloads or the Python folder.",
            })

        latest_text = ".".join(str(part) for part in latest_version)
        return jsonify({
            "status": "update_available" if latest_version > current_version else "current",
            "current_version": app.config["APP_VERSION"],
            "latest_version": latest_text,
            "file_name": latest_path.name,
            "folder": str(latest_path.parent),
            "message": (
                f"Update v{latest_text} is available."
                if latest_version > current_version
                else f"You already have the latest version found: v{latest_text}."
            ),
        })
    except Exception as error:
        log_error_code("E1401", f"Update check failed: {error}")
        return api_error("E1401", "Could not check for the latest Job Finder update.", 500)


@app.route("/install-update", methods=["POST"])
def install_update():
    data = request.get_json(silent=True) or {}
    requested = (data.get("file_name") or "").strip()
    latest_version, latest_path = find_latest_update_zip()
    if latest_path is None:
        return api_error("E1402", "No update ZIP was found.", 404)
    current_version = tuple(int(part) for part in app.config["APP_VERSION"].split("."))
    if latest_version <= current_version:
        return api_error("E1406", "No newer update is available to install.", 409)
    if requested and latest_path.name != requested:
        return jsonify({
            "status": "update_changed",
            "code": "E1403",
            "message": "A newer update ZIP was found. Review the new version before installing.",
            "latest_version": ".".join(map(str, latest_version)),
            "file_name": latest_path.name,
        }), 409
    if not UPDATE_SCRIPT.exists():
        return api_error("E1404", "update.ps1 was not found.", 500)
    try:
        creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        subprocess.Popen(
            ["powershell.exe", "-ExecutionPolicy", "Bypass", "-File", str(UPDATE_SCRIPT), "-ZipPath", str(latest_path), "-CurrentPid", str(os.getpid())],
            cwd=str(BASE_DIR),
            creationflags=creationflags,
        )
        return jsonify({"status": "updating", "version": ".".join(map(str, latest_version)), "file_name": latest_path.name})
    except OSError as error:
        log_error_code("E1405", f"Could not launch updater: {error}")
        return api_error("E1405", "Could not start the update installer.", 500)


@app.route("/app-version")
def app_version():
    return jsonify({"version": app.config["APP_VERSION"]})


def extension_dist_dir():
    return Path(read_tuning_settings().get("extension_dist_dir") or BASE_DIR.parent / "web-job-scraper" / "dist")


def latest_extension_build():
    """(version text, path) of the newest web-job-scraper-vX.Y.Z.xpi in web-job-scraper\\dist, or (None, None)."""
    folder = extension_dist_dir()
    builds = []
    if folder.is_dir():
        for path in folder.iterdir():
            match = EXTENSION_FILE.match(path.name)
            if match and path.is_file():
                builds.append((tuple(int(part) for part in match.groups()), path))
    if not builds:
        return None, None
    version, path = max(builds)
    return ".".join(map(str, version)), path


@app.route("/extension/updates.json")
def extension_updates():
    """Firefox's "Check for Updates" for the Web Job Scraper extension reads this (its manifest's update_url)."""
    version, path = latest_extension_build()
    updates = []
    if version:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        updates.append({"version": version, "update_link": f"{request.host_url}extension/{path.name}",
                        "update_hash": f"sha256:{digest}"})
    return jsonify({"addons": {EXTENSION_ID: {"updates": updates}}})


@app.route("/extension/fit-profile")
def extension_fit_profile():
    """What Web Job Scraper needs to mark LinkedIn jobs that may fit: your titles, skills and work preferences,
    plus Job Finder's skill names and spellings so Job Fit is worked out the same way as on the Dashboard.

    No CORS header on purpose: only the extension (which has permission for this address) can read it; an
    ordinary web page asking for it gets nothing back.
    """
    profile = profile_store.get_user_profile()
    titles = [spelling_fix(title) for title in
              [profile.get("primary_job_title") or ""] + list(profile.get("job_titles") or [])]  # typos fixed, as in searches
    return jsonify({
        "titles": [title for i, title in enumerate(titles) if title and title.casefold() not in
                   {t.casefold() for t in titles[:i]}],
        "skills": profile.get("skills") or [],
        "work_preferences": profile.get("work_preferences") or [],
        "blocked_companies": blocklists.get_blocked_companies(),  # companies you blocked in Job Finder: hidden on LinkedIn too
        "skill_aliases": SKILL_ALIASES,
        "ambiguous_skills": sorted(AMBIGUOUS_SKILLS),
    })


@app.route("/extension/distances")
def extension_distances():
    """Estimated distance and 6 a.m. drive time from the home ZIP to each place (places separated by |), for the
    LinkedIn markers. Same estimate as the Dashboard (travel.py); nothing is looked up online. No CORS header,
    so only the extension can read it."""
    profile = profile_store.get_user_profile()
    home_zip, home_state = profile.get("home_zip") or "", profile.get("state") or ""
    places = [place.strip()[:120] for place in (request.args.get("places") or "").split("|") if place.strip()][:100]
    return jsonify({"home_zip": home_zip,
                    "places": {place: describe_trip(home_zip, None, place, home_state) if home_zip else None
                               for place in places}})


@app.route("/extension/<name>")
def extension_file(name):
    if not EXTENSION_FILE.match(name):
        abort(404)
    return send_from_directory(extension_dist_dir(), name, mimetype="application/x-xpinstall")


@app.route("/")
def home():
    now = datetime.now(ZoneInfo("America/New_York"))

    print()
    print("=" * 60)
    print("PAGE REFRESH — " f"{now.strftime('%B %d, %Y at %I:%M:%S %p %Z')}")
    print("=" * 60)

    ensure_keep_column()
    schema.ensure_job_tracking_columns()
    companies = get_companies()

    running, mode = scraper_status()
    profile = profile_store.get_user_profile()
    add_job_fit(companies, profile.get("skills", []))
    recent_search = get_search_history(limit=1)
    latest_search_at = recent_search[0].get("searched_at") if recent_search else None
    skipped = []
    if SEARCH_SKIPS_FILE.exists():
        try:
            for item in reversed(list(latest_decisions(SEARCH_SKIPS_FILE).values())):
                # Older searches recorded PDFs as skips; they are now ignored entirely.
                if item.get("reason") != "Passed" and not is_pdf_url(item.get("url")):
                    skipped.append(item)
                if len(skipped) >= 20:
                    break
        except (OSError, ValueError):
            skipped = []

    return render_template(
        "index.html",
        companies=companies,
        skipped=skipped,
        latest_search_at=latest_search_at,
        scraper_running=running,
        scraper_mode=mode or "",
        profile_cities=profile.get("cities", []),
        profile_job_titles=profile.get("job_titles", []),
        profile_state=profile.get("state", ""),
        profile_work_preferences=profile.get("work_preferences", []),
    )


@app.route("/dashboard")
def user_dashboard():
    ensure_keep_column()
    schema.ensure_job_tracking_columns()
    status_filter = request.args.get("status", "").strip()[:30]
    state_filter = request.args.get("state", "").strip()[:100]
    title_filter = request.args.get("title", "").strip()[:255]
    sort_by = request.args.get("sort", "date_desc").strip()[:30]
    companies = get_kept_companies(status_filter, state_filter, title_filter, sort_by)
    profile = profile_store.get_user_profile()
    add_job_fit(companies, profile.get("skills", []))
    add_drive_times(companies, profile.get("home_zip"), profile.get("state") or "")
    resume_skills = resume_skill_suggestions(RESUME_FOLDER, profile.get("skills", []))
    # With no work history saved yet, the form is filled in from the uploaded résumé (nothing is saved until Save Profile).
    resume_history = [] if profile.get("work_history") else parse_work_history(uploaded_resume(RESUME_FOLDER)[1])
    demanded_skills = [{"skill": skill, "count": count} for skill, count in listing_skill_demand(profile.get("skills", []))]
    return render_template(
        "user-dashboard.html",
        companies=companies,
        profile=profile,
        counts=get_dashboard_counts(),
        search_history=get_search_history(),
        skill_suggestions=profile_skill_suggestions(profile),
        resume_source=resume_skills[0], resume_skills=resume_skills[1],
        demanded_skills=demanded_skills,
        resume_history=resume_history,
        fit_updated=request.args.get("fit_updated", type=int),
        filters={"status": status_filter, "state": state_filter, "title": title_filter, "sort": sort_by},
    )


# Search tuning options that live in settings.json: key -> (label, help, kind, minimum, maximum, default).
TUNING_FIELDS = {
    "searxng_timeout_minutes": ("Search time limit (minutes)", "A search stops after this long.", int, 1, 240, 60),
    "max_search_results": ("Jobs to find per search", "A search stops once it has saved this many new jobs.", int, 1, 100, 10),
    "max_search_pages": ("Result pages per query", "How many pages of results to read for each search query.", int, 1, 50, 20),
    "request_delay_seconds": ("Pause between requests to one site (seconds)", "Lower is faster; keep at 1 or more to stay polite.", float, 0, 10, 1),
    "search_query_delay_seconds": ("Pause between search-engine queries (seconds)", "Too low can get the engines to rate-limit Job Finder.", float, 0, 10, 2),
    "website_timeout_seconds": ("Wait for a slow page (seconds)", "A page that has not answered by then is skipped.", int, 5, 60, 15),
    "parallel_page_fetches": ("Pages downloaded at once", "More is faster but uses more of your connection.", int, 1, 12, 6),
    "stop_after_empty_queries": ("Stop after this many empty queries in a row", "Many empty queries usually mean the engines are refusing us.", int, 2, 30, 8),
}
TUNING_SWITCHES = {
    "usa_only": ("U.S. jobs only", "Skip jobs that are outside the United States or unverified.", True),
    "exclude_internships": ("Skip internships and co-ops", "Leave out jobs titled intern, internship or co-op.", True),
    "related_titles": ("Also match closely related titles", "Recognise titles such as Multimedia Designer or Production Artist when you typed Designer or Production Specialist.", True),
    "start_docker_automatically": ("Start Docker automatically", "Starts Docker Desktop for the web search.", True),
    "stop_docker_when_finished": ("Stop Docker when finished", "Closes Docker Desktop after the search.", True),
}


def read_tuning_settings():
    try:
        return json.loads(webfiles.SETTINGS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def apply_tuning_form(form, current):
    """Return (new settings, error). Only the known options change; every other key in the file is kept."""
    updated = dict(current)
    for key, (label, _, kind, low, high, _default) in TUNING_FIELDS.items():
        raw = str(form.get(key, "")).strip()
        try:
            value = kind(raw)
        except ValueError:
            return None, f"{label} must be a number."
        if not low <= value <= high:
            return None, f"{label} must be between {low} and {high}."
        updated[key] = int(value) if kind is int or float(value).is_integer() else value
    for key in TUNING_SWITCHES:
        updated[key] = form.get(key) == "on"
    return updated, None


@app.route("/tuning")
def tuning_page():
    current = read_tuning_settings()
    health = read_health()
    if health:
        try:
            health["updated_text"] = datetime.fromisoformat(health["updated"]).astimezone().strftime("%b %d, %Y %I:%M %p")
        except (KeyError, ValueError):
            health["updated_text"] = health.get("updated", "")
    return render_template("tuning.html", fields=TUNING_FIELDS, switches=TUNING_SWITCHES, values=current,
                           health=health, statuses=HEALTH_STATUSES, saved=request.args.get("saved"),
                           error=request.args.get("error"))


@app.route("/tuning/settings", methods=["POST"])
def save_tuning_settings():
    updated, error = apply_tuning_form(request.form, read_tuning_settings())
    if error:
        return redirect("/tuning?error=" + quote(error) + "#search-settings")
    temporary = Path(str(webfiles.SETTINGS_FILE) + ".tmp")
    try:
        temporary.write_text(json.dumps(updated, indent="\t") + "\n", encoding="utf-8")
        os.replace(temporary, webfiles.SETTINGS_FILE)
    except OSError as error:
        log_error_code("E4101", f"Could not save settings.json: {error}")
        return redirect("/tuning?error=" + quote("Could not save the settings file.") + "#search-settings")
    return redirect("/tuning?saved=1#search-settings")


@app.route("/credibility-scores")
def credibility_scores():
    return render_template("credibility-scores.html")


@app.route("/rejected-listings")
def rejected_listings():
    schema.ensure_job_tracking_columns()
    decisions = latest_decisions(SEARCH_SKIPS_FILE)
    search_skips = []
    for event in reversed(list(decisions.values())):
        if event.get("reason") == "Passed" or is_pdf_url(event.get("url")):
            continue
        checked_at = event.get("checked_at") or ""
        next_check = "Next search"
        if checked_at and event.get("reason") in TTL_HOURS:
            try:
                expires = datetime.fromisoformat(checked_at.replace("Z", "+00:00")) + timedelta(hours=TTL_HOURS[event["reason"]])
                next_check = expires.astimezone(ZoneInfo("America/New_York")).strftime("%b %d, %Y %I:%M %p") if expires > datetime.now(timezone.utc) else "Next search"
            except (ValueError, TypeError):
                pass
        search_skips.append({**event, "next_check": next_check})
    # The skipped pages run to thousands, so only one page of them is sent (and searched on the server).
    skip_query = (request.args.get("skip_q") or "").strip()[:200]
    if skip_query:
        needle = skip_query.casefold()
        search_skips = [item for item in search_skips
                        if needle in " ".join(str(item.get(k) or "") for k in ("title", "reason", "url")).casefold()]
    skip_total = len(search_skips)
    skip_pages = max(1, -(-skip_total // SKIPS_PER_PAGE))
    try:
        skip_page = min(max(int(request.args.get("skip_page", 1)), 1), skip_pages)
    except ValueError:
        skip_page = 1
    search_skips = search_skips[(skip_page - 1) * SKIPS_PER_PAGE:skip_page * SKIPS_PER_PAGE]
    return render_template(
        "rejected-listings.html",
        companies=get_rejected_companies(),
        search_skips=search_skips, skip_total=skip_total, skip_page=skip_page, skip_pages=skip_pages, skip_query=skip_query,
        blocked_domains=blocklists.get_blocked_domains(),
        blocked_companies=blocklists.get_blocked_companies(),
        blocked_domain_info=block_details("domains", blocklists.get_blocked_domains()),
        blocked_company_info=block_details("companies", blocklists.get_blocked_companies()),
        company_notice=request.args.get("company_notice", ""),
        domain_notice=request.args.get("domain_notice", ""),
    )


@app.route("/reject-listing/<int:company_id>", methods=["POST"])
def reject_listing(company_id):
    schema.ensure_job_tracking_columns()
    wants_json = request.headers.get("X-Requested-With") == "fetch" or request.accept_mimetypes.best == "application/json"
    connection = None
    cursor = None
    domain = None
    reason = (request.get_json(silent=True) or {}).get("reason") if request.is_json else request.form.get("reason")
    if reason not in {"wrong_role", "wrong_location", "not_a_job", "duplicate", "other"}:
        reason = "other"
    try:
        connection = db.connect()
        cursor = connection.cursor(dictionary=True)
        cursor.execute("SELECT domain FROM companies WHERE id = %s", (company_id,))
        row = cursor.fetchone()
        if not row:
            if wants_json:
                return api_error("E3201", "The listing could not be found.", 404)
            return redirect(request.referrer or "/")

        domain = row.get("domain")
        cursor.execute(
            """
            UPDATE companies
            SET pre_reject_kept = CASE WHEN is_rejected = 0 THEN is_kept ELSE pre_reject_kept END,
                pre_reject_status = CASE WHEN is_rejected = 0 THEN application_status ELSE pre_reject_status END,
                is_rejected = 1, is_kept = 0, application_status = 'Rejected',
                rejected_at = CURRENT_TIMESTAMP, rejection_reason = %s, rejected_by = 'user'
            WHERE id = %s
            """,
            (reason, company_id),
        )
        connection.commit()
        if wants_json:
            return jsonify({"status": "rejected", "company_id": company_id, "domain": domain})
    except Error as error:
        print("Could not reject listing.")
        print(error)
        if wants_json:
            return api_error("E3202", "Could not reject the listing.", 500)
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None and connection.is_connected():
            connection.close()

    return redirect(request.referrer or "/")


@app.route("/restore-rejected/<int:company_id>", methods=["POST"])
def restore_rejected(company_id):
    schema.ensure_job_tracking_columns()
    wants_json = request.headers.get("X-Requested-With") == "fetch" or request.accept_mimetypes.best == "application/json"
    connection = None
    cursor = None
    domain = None
    try:
        connection = db.connect()
        cursor = connection.cursor(dictionary=True)
        cursor.execute("SELECT domain, pre_reject_kept FROM companies WHERE id = %s AND is_rejected = 1", (company_id,))
        row = cursor.fetchone()
        if not row:
            if wants_json:
                return api_error("E3203", "The rejected listing could not be found.", 404)
            return redirect("/rejected-listings")

        domain = row.get("domain")
        # Older rejections have no saved state; they return as unsaved search results.
        cursor.execute(
            """
            UPDATE companies
            SET is_rejected = 0, is_kept = COALESCE(pre_reject_kept, 0),
                application_status = COALESCE(pre_reject_status, 'None'),
                rejected_at = NULL, rejection_reason = NULL, rejected_by = NULL,
                pre_reject_kept = NULL, pre_reject_status = NULL
            WHERE id = %s
            """,
            (company_id,),
        )
        connection.commit()
        if wants_json:
            return jsonify({"status": "restored", "company_id": company_id, "domain": domain,
                            "kept": bool(row.get("pre_reject_kept"))})
    except Error as error:
        print("Could not restore rejected listing.")
        print(error)
        if wants_json:
            return api_error("E3204", "Could not restore the listing.", 500)
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None and connection.is_connected():
            connection.close()

    return redirect("/rejected-listings")


@app.route("/block-domain/<int:company_id>", methods=["POST"])
def block_domain(company_id):
    schema.ensure_job_tracking_columns()
    connection = None
    cursor = None
    try:
        connection = db.connect()
        cursor = connection.cursor(dictionary=True)
        cursor.execute("SELECT domain, source_type FROM companies WHERE id = %s", (company_id,))
        row = cursor.fetchone()
        if row and row.get("source_type") in FEED_NAMES:
            return api_error("E3212", "This is the feed domain, not the employer domain. Block the company instead.", 400)
        if not row or not row.get("domain"):
            return api_error("E3210", "No domain was available to block.", 404)
        add_domain_to_blocklist(row["domain"])
        return jsonify({"status": "blocked", "domain": row["domain"]})
    except Error as error:
        return api_error("E3211", "Could not block the domain.", 500)
    finally:
        if cursor is not None: cursor.close()
        if connection is not None and connection.is_connected(): connection.close()


@app.route("/save-profile", methods=["POST"])
def save_profile():
    first_name = request.form.get("first_name", "").strip()[:100]
    last_name = request.form.get("last_name", "").strip()[:100]
    state = request.form.get("state", "").strip()[:100]
    raw_job_titles = request.form.get("job_titles", "")[:5000]
    job_titles = []

    for part in re.split(r"[,\n]+", raw_job_titles):
        title = proper_title(part.strip()[:255])
        if title and title.lower() not in {item.lower() for item in job_titles}:
            job_titles.append(title)

    try:
        cities = json.loads(request.form.get("cities_json", "[]") or "[]")
    except json.JSONDecodeError:
        cities = []
    def read_list(key):
        try:
            value = json.loads(request.form.get(key, "[]"))
            return value if isinstance(value, list) else []
        except (TypeError, ValueError):
            return []
    home_location = request.form.get("home_location", "").strip()[:150]
    home_zip = re.sub(r"\D", "", request.form.get("home_zip", ""))[:5]
    if home_zip and len(home_zip) != 5:
        home_zip = ""  # a half-typed ZIP is dropped rather than saved
    # The primary title has its own box; it is always searched too, so it leads the title list.
    primary = proper_title(request.form.get("primary_job_title", "").strip()[:255])
    if primary:
        job_titles = [primary] + [title for title in job_titles if title.casefold() != primary.casefold()]
    previous_skills = {str(skill).casefold() for skill in profile_store.get_user_profile().get("skills", [])}
    avatar = request.form.get("avatar_data", "")
    if avatar and (not re.fullmatch(r"data:image/jpeg;base64,[A-Za-z0-9+/=]+", avatar) or len(avatar) > 550000):
        abort(400)
    profile_store.save_user_profile(first_name, last_name, state, job_titles, cities,
                      home_location=home_location, home_zip=home_zip, primary_job_title=primary,
                      skills=read_list("skills_json"), work_history=read_list("work_history_json"),
                      avatar_data=avatar if "avatar_data" in request.form else None,
                      work_preferences=[value for value in request.form.getlist("work_preferences")
                                        if value in {"Part-time", "Full-time", "Contract", "Freelance / Gig", "Remote", "Hybrid", "Onsite"}])
    # Changed skills: the percentages on the pages follow at once (they are worked out on each load); listings saved
    # earlier are re-read so they also list skills the current skills list recognizes.
    if {str(skill).casefold() for skill in read_list("skills_json")} != previous_skills:
        return redirect(f"/dashboard?fit_updated={refresh_job_fit()}")
    return redirect("/dashboard")


@app.route("/profile/save-title", methods=["POST"])
def save_profile_title():
    data = request.get_json(silent=True) or {}
    title = proper_title(str(data.get("title", "")).strip()[:255])
    if not title:
        return api_error("E3301", "Enter a job title.")
    profile = profile_store.get_user_profile()
    if title.casefold() in {s.casefold() for s in profile["job_titles"]}:
        return jsonify({"status": "already_saved"})
    profile["job_titles"].append(title)
    if not profile_store.save_user_profile(profile["first_name"], profile["last_name"], profile["state"],
                             profile["job_titles"], profile["cities"]):
        return api_error("E3302", "Could not save the job title.", 500)
    return jsonify({"status": "saved"})


@app.route("/profile/parse-resume", methods=["POST"])
def parse_resume():
    upload = request.files.get("resume")
    if not upload or not upload.filename:
        return api_error("E3310", "Choose a PDF or Word document.")
    extension = Path(upload.filename).suffix.lower()
    if extension not in (".pdf", ".docx"):
        return api_error("E3311", "Use a PDF or DOCX résumé.")
    data = upload.read(5 * 1024 * 1024 + 1)
    if len(data) > 5 * 1024 * 1024:
        return api_error("E3312", "The résumé must be under 5 MB.")
    try:
        from io import BytesIO
        if extension == ".pdf":
            from pypdf import PdfReader
            text = "\n".join(page.extract_text(extraction_mode="layout") or "" for page in PdfReader(BytesIO(data)).pages[:20])
        else:
            from docx import Document
            document = Document(BytesIO(data))
            text = "\n".join([p.text for p in document.paragraphs] +
                             [cell.text for table in document.tables for row in table.rows for cell in row.cells])
    except Exception as error:
        print(f"Could not parse résumé: {error}")
        return api_error("E3313", "Could not read that résumé. Try another PDF or DOCX.")
    if not text.strip():
        return api_error("E3314", "No selectable text was found. A scanned image résumé needs OCR.")
    return jsonify({"status": "ok", "suggestions": resume_suggestions(text[:250000])})


@app.route("/save-kept", methods=["POST"])
def save_kept():
    ensure_keep_column()

    data = request.get_json(silent=True) or {}
    company_ids = data.get("company_ids", [])

    if not isinstance(company_ids, list):
        return jsonify({"status": "error", "message": "Invalid company list."}), 400

    cleaned_ids = []
    for company_id in company_ids:
        try:
            cleaned_ids.append(int(company_id))
        except (TypeError, ValueError):
            continue

    if not cleaned_ids:
        return jsonify({"status": "error", "message": "Select at least one result to keep."}), 400

    connection = None
    cursor = None

    try:
        connection = db.connect()
        cursor = connection.cursor()
        placeholders = ",".join(["%s"] * len(cleaned_ids))
        cursor.execute(
            f"UPDATE companies SET is_kept = 1, application_status = 'Saved' WHERE id IN ({placeholders})",
            cleaned_ids,
        )
        connection.commit()
        return jsonify({"status": "saved", "count": cursor.rowcount})
    except Error as error:
        return jsonify({"status": "error", "message": str(error)}), 500
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None and connection.is_connected():
            connection.close()


@app.route("/unsave-kept/<int:company_id>", methods=["POST"])
def unsave_kept(company_id):
    schema.ensure_job_tracking_columns()
    connection = None
    cursor = None
    try:
        connection = db.connect()
        cursor = connection.cursor()
        cursor.execute(
            "UPDATE companies SET is_kept = 0, application_status = 'None' WHERE id = %s",
            (company_id,),
        )
        connection.commit()
        return jsonify({"status": "unsaved", "company_id": company_id})
    except Error as error:
        return api_error("E3102", "Could not unsave the listing.", 500)
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None and connection.is_connected():
            connection.close()


def _finish_graceful_stop(process):
    """Wait for a search to clean up after a stop request; force it only as a fallback."""
    try:
        process.wait(timeout=GRACEFUL_STOP_SECONDS)
    except subprocess.TimeoutExpired:
        log_error_code("E2106", "Job Finder did not stop in time; forcing it and stopping SearXNG.")
        process.kill()
        process.wait(timeout=10)
        # The killed process could not run its own cleanup.
        try:
            subprocess.run(["docker", "stop", "searxng"], capture_output=True, timeout=60,
                           creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        except (OSError, subprocess.TimeoutExpired) as error:
            log_error_code("E2106", f"Could not stop SearXNG: {error}")
    finally:
        webfiles.STOP_REQUEST_FILE.unlink(missing_ok=True)


@app.route("/stop-search", methods=["POST"])
def stop_search():
    global scraper_process, scraper_mode, scraper_last_error, scraper_stopping
    with scraper_lock:
        if scraper_process is None or scraper_process.poll() is not None:
            scraper_process = None
            scraper_mode = None
            return jsonify({"status": "stopped", "message": "No Job Finder process was running."})
        if scraper_mode in ("search", "replacement"):
            # Searches own SearXNG and Docker, so let them shut those down themselves.
            if not scraper_stopping:
                scraper_stopping = True
                try:
                    webfiles.STOP_REQUEST_FILE.touch()
                except OSError as error:
                    scraper_stopping = False
                    log_error_code("E2105", f"Could not request a stop: {error}")
                    return api_error("E2105", "Could not stop the current Job Finder action.", 500)
                threading.Thread(target=_finish_graceful_stop, args=(scraper_process,), daemon=True).start()
            return jsonify({"status": "stopping"})
        try:
            scraper_process.terminate()
            try:
                scraper_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                scraper_process.kill()
                scraper_process.wait(timeout=5)
            scraper_process = None
            scraper_mode = None
            scraper_last_error = None
            return jsonify({"status": "stopped"})
        except OSError as error:
            return api_error("E2105", "Could not stop the current Job Finder action.", 500)


@app.route("/update-kept/<int:company_id>", methods=["POST"])
def update_kept(company_id):
    schema.ensure_job_tracking_columns()
    application_status = request.form.get("application_status", "None").strip()
    notes = request.form.get("notes", "").strip()[:5000]
    if application_status not in APPLICATION_STATUSES:
        application_status = "None"

    connection = None
    cursor = None
    try:
        connection = db.connect()
        cursor = connection.cursor()
        cursor.execute(
            "UPDATE companies SET application_status = %s, notes = %s WHERE id = %s AND is_kept = 1",
            (application_status, notes, company_id),
        )
        connection.commit()
    except Error as error:
        print("Could not update saved result.")
        print(error)
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None and connection.is_connected():
            connection.close()
    # The #job anchor reopens this card on the Dashboard, where saved jobs start collapsed.
    return redirect((request.referrer or "/dashboard") + f"#job-{company_id}")


@app.route("/delete-kept/<int:company_id>", methods=["POST"])
def delete_kept(company_id):
    ensure_keep_column()
    connection = None
    cursor = None

    try:
        connection = db.connect()
        cursor = connection.cursor()
        cursor.execute(
            "DELETE FROM companies WHERE id = %s AND is_kept = 1",
            (company_id,),
        )
        connection.commit()
    except Error as error:
        print()
        print("Could not delete saved result.")
        print(error)
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None and connection.is_connected():
            connection.close()

    return redirect("/dashboard")


def validate_search_criteria(job_title, state, cities=None):
    job_title = (job_title or "").strip()[:1000]
    state = (state or "").strip()[:100]

    if not job_title:
        return None, None, None, "Enter a job title."

    if not state:
        return None, None, None, "Enter a state."

    cleaned_cities = []
    allowed_radii = {5, 10, 15, 20, 30, 50}
    if isinstance(cities, list):
        seen = set()
        for item in cities[:25]:
            if not isinstance(item, dict):
                continue
            city = str(item.get("city", "")).strip()[:150]
            try:
                radius = int(item.get("radius", 50))
            except (TypeError, ValueError):
                radius = 50
            if city and radius in allowed_radii and city.lower() not in seen:
                seen.add(city.lower())
                cleaned_cities.append({"city": city, "radius": radius})

    return job_title, state, cleaned_cities, None


def launch_search_process(job_title, state, cities=None, mode="search"):
    global scraper_process, scraper_mode, scraper_started_at

    job_title, state, cities, validation_error = validate_search_criteria(job_title, state, cities)
    if validation_error:
        return jsonify({"status": "error", "message": validation_error}), 400

    with scraper_lock:
        if scraper_process is not None and scraper_process.poll() is None:
            return jsonify({"status": "already_running", "mode": scraper_mode}), 200

        if not JOB_FINDER_PATH.exists():
            return jsonify({"status": "error", "message": "job_finder.py was not found."}), 500

        # Keep Search and User Profile on the same saved job-title/location data,
        # but only once this search is actually going to start.
        if mode != "replacement":
            try:
                profile = profile_store.get_user_profile()
                titles = []
                for part in job_title.split(","):
                    title = part.strip()[:255]
                    if title and title.lower() not in {item.lower() for item in titles}:
                        titles.append(title)
                profile_store.save_user_profile(
                    profile.get("first_name", ""),
                    profile.get("last_name", ""),
                    state,
                    titles,
                    cities,
                )
            except Exception as error:
                log_error_code("E3301", f"Could not sync search criteria to profile: {error}")

        try:
            creationflags = 0
            if os.name == "nt":
                creationflags = subprocess.CREATE_NO_WINDOW

            global scraper_last_error
            scraper_last_error = None
            # A leftover request from an earlier stop would end this search immediately.
            webfiles.STOP_REQUEST_FILE.unlink(missing_ok=True)
            with webfiles.SCRAPER_LOG_FILE.open("a", encoding="utf-8") as log_file:
                log_file.write(
                    f"\n=== {datetime.now().isoformat(timespec='seconds')} | {mode} | "
                    f"{job_title} | {state} ===\n"
                )
                log_file.flush()
                scraper_process = subprocess.Popen(
                    [
                        sys.executable,
                        "-u",
                        str(JOB_FINDER_PATH),
                        "--job-title",
                        job_title,
                        "--state",
                        state,
                        "--cities-json",
                        json.dumps(cities or []),
                    ] + (["--max-new", "1"] if mode == "replacement" else []),
                    cwd=str(BASE_DIR),
                    creationflags=creationflags,
                    stdout=log_file,
                    stderr=subprocess.STDOUT,
                )
            scraper_mode = mode
            scraper_started_at = time.monotonic()
            if mode != "replacement":
                record_search_history(job_title, state, cities)
        except OSError as error:
            scraper_process = None
            scraper_mode = None
            return jsonify({"status": "error", "message": str(error)}), 500

    return jsonify({"status": "started"}), 202


@app.route("/job-title-matches")
def job_title_matches():
    """Type-ahead for job title boxes: real job titles that match what has been typed so far."""
    return jsonify({"matches": title_matches((request.args.get("q") or "")[:100])})


_home_state_cache = {"value": "", "at": 0.0}


def _home_state():
    """The profile's home state ("MD"), re-read at most once a minute so typing doesn't hit the database."""
    if time.monotonic() - _home_state_cache["at"] > 60:
        _home_state_cache.update(value=(profile_store.get_user_profile().get("state") or "")[:2], at=time.monotonic())
    return _home_state_cache["value"]


@app.route("/city-matches")
def city_matches_route():
    """Type-ahead for the city boxes: U.S. places that start with what was typed, near home first."""
    return jsonify({"matches": city_matches((request.args.get("q") or "")[:100], _home_state())})


@app.route("/job-title-suggestions")
def job_title_suggestions():
    titles = (request.args.get("titles") or "").strip()[:1000]
    return jsonify({"suggestions": related_job_title_suggestions(titles)})

@app.route("/start-search", methods=["POST"])
def start_search():
    data = request.get_json(silent=True) or {}
    return launch_search_process(
        data.get("job_title"),
        data.get("state"),
        data.get("cities"),
        mode="search",
    )


@app.route("/replace-result", methods=["POST"])
def replace_result():
    history = get_search_history(limit=1)
    if not history:
        return jsonify({"status": "no_results", "message": "Start a search to save criteria before requesting a replacement."}), 200
    search = history[0]
    try:
        cities = json.loads(search.get("cities_json") or "[]")
    except (TypeError, ValueError):
        cities = []
    return launch_search_process(search.get("job_title"), search.get("state"), cities, mode="replacement")


@app.route("/refresh-search", methods=["POST"])
def refresh_search():
    """Recheck the result rows currently shown without changing search criteria."""
    global scraper_process, scraper_mode, scraper_started_at, scraper_last_error
    data = request.get_json(silent=True) or {}
    ids = data.get("company_ids")
    if not isinstance(ids, list) or len(ids) > 500 or any(type(value) is not int or value <= 0 for value in ids):
        return jsonify({"status": "error", "message": "Invalid result selection."}), 400
    ids = list(dict.fromkeys(ids))
    if not ids:
        return jsonify({"status": "no_results", "message": "There are no displayed listings to refresh."})
    with scraper_lock:
        if scraper_process is not None and scraper_process.poll() is None:
            return jsonify({"status": "already_running", "mode": scraper_mode}), 200
        if not JOB_FINDER_PATH.exists():
            return jsonify({"status": "error", "message": "job_finder.py was not found."}), 500
        try:
            flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            scraper_last_error = None
            with webfiles.SCRAPER_LOG_FILE.open("a", encoding="utf-8") as log_file:
                log_file.write(f"\n=== {datetime.now().isoformat(timespec='seconds')} | update-existing ===\n")
                log_file.flush()
                scraper_process = subprocess.Popen(
                    [sys.executable, "-u", str(JOB_FINDER_PATH), "--update-existing", "--update-ids", ",".join(map(str, ids))],
                    cwd=str(BASE_DIR), creationflags=flags, stdout=log_file, stderr=subprocess.STDOUT,
                )
            scraper_mode = "refresh"
            scraper_started_at = time.monotonic()
        except OSError as error:
            scraper_process = None
            scraper_mode = None
            return jsonify({"status": "error", "message": str(error)}), 500
    return jsonify({"status": "started"}), 202


@app.route("/update-existing", methods=["POST"])
def update_existing():
    global scraper_process, scraper_mode, scraper_started_at

    with scraper_lock:
        if scraper_process is not None and scraper_process.poll() is None:
            return jsonify({"status": "already_running", "mode": scraper_mode}), 200

        if not JOB_FINDER_PATH.exists():
            return jsonify({"status": "error", "message": "job_finder.py was not found."}), 500

        try:
            creationflags = 0
            if os.name == "nt":
                creationflags = subprocess.CREATE_NO_WINDOW

            global scraper_last_error
            scraper_last_error = None
            with webfiles.SCRAPER_LOG_FILE.open("a", encoding="utf-8") as log_file:
                log_file.write(
                    f"\n=== {datetime.now().isoformat(timespec='seconds')} | update-existing ===\n"
                )
                log_file.flush()
                scraper_process = subprocess.Popen(
                    [sys.executable, "-u", str(JOB_FINDER_PATH), "--update-existing"],
                    cwd=str(BASE_DIR),
                    creationflags=creationflags,
                    stdout=log_file,
                    stderr=subprocess.STDOUT,
                )
            scraper_mode = "update"
            scraper_started_at = time.monotonic()
        except OSError as error:
            scraper_process = None
            scraper_mode = None
            return jsonify({"status": "error", "message": str(error)}), 500

    return jsonify({"status": "started"}), 202


@app.route("/captures/pending")
def captures_pending():
    """Capture files the Web Job Scraper extension left in Downloads, for the import prompt."""
    downloads_dir, searches_dir = capture_dirs(read_tuning_settings(), BASE_DIR)
    try:
        files = pending_files(downloads_dir)
    except OSError as error:
        return api_error("E2120", f"Could not read {downloads_dir}: {error}", 500)
    return jsonify({
        "count": len(files),
        "jobs": sum(item["jobs"] for item in files),
        "from": str(downloads_dir),
        "to": str(searches_dir),
        # Changes whenever a file is added or rewritten, so "Not now" only hides the prompt until something new arrives.
        "signature": ";".join(f"{item['relative']}@{int(item['modified'])}" for item in files),
    })


@app.route("/captures/import", methods=["POST"])
def captures_import():
    """Move the waiting capture files into web-job-scraper\\searches, then run job_finder.py --import-captures."""
    global scraper_process, scraper_mode, scraper_started_at, scraper_last_error
    downloads_dir, searches_dir = capture_dirs(read_tuning_settings(), BASE_DIR)
    with scraper_lock:
        if scraper_process is not None and scraper_process.poll() is None:
            return jsonify({"status": "already_running", "mode": scraper_mode}), 200
        try:
            moved = move_pending(downloads_dir, searches_dir)
        except OSError as error:
            return api_error("E2121", f"Could not move the capture files to {searches_dir}: {error}", 500)
        try:
            flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            scraper_last_error = None
            with webfiles.SCRAPER_LOG_FILE.open("a", encoding="utf-8") as log_file:
                log_file.write(f"\n=== {datetime.now().isoformat(timespec='seconds')} | import | "
                               f"{len(moved)} capture file(s) ===\n")
                log_file.flush()
                scraper_process = subprocess.Popen(
                    [sys.executable, "-u", str(JOB_FINDER_PATH), "--import-captures"],
                    cwd=str(BASE_DIR), creationflags=flags, stdout=log_file, stderr=subprocess.STDOUT,
                )
            scraper_mode = "import"
            scraper_started_at = time.monotonic()
        except OSError as error:
            scraper_process = None
            scraper_mode = None
            return api_error("E2121", f"Could not start the import: {error}", 500)
    return jsonify({"status": "started", "moved": len(moved)}), 202


def import_progress_from_log():
    if not webfiles.SCRAPER_LOG_FILE.exists():
        return "Preparing the import"
    try:
        with webfiles.SCRAPER_LOG_FILE.open("rb") as log_file:
            size = log_file.seek(0, 2)
            log_file.seek(max(0, size - 65536))
            tail = log_file.read().decode("utf-8", errors="replace")
    except OSError:
        return "Preparing the import"
    current = tail.rsplit("| import |", 1)[-1]
    matches = list(re.finditer(r"Importing (\d+)/(\d+): ([^\r\n]+)", current))
    if matches:
        latest = matches[-1]
        return f"Checking {latest.group(1)} of {latest.group(2)}: {latest.group(3)[:70]}"
    return "Preparing the import"


def update_progress_from_log():
    if not webfiles.SCRAPER_LOG_FILE.exists():
        return "Preparing existing results"
    try:
        with webfiles.SCRAPER_LOG_FILE.open("rb") as log_file:
            size = log_file.seek(0, 2)
            log_file.seek(max(0, size - 65536))
            tail = log_file.read().decode("utf-8", errors="replace")
    except OSError:
        return "Preparing existing results"
    current = tail.rsplit("| update-existing ===", 1)[-1]
    matches = list(re.finditer(r"Updating (\d+)/(\d+): ([^\r\n]+)", current))
    if matches:
        latest = matches[-1]
        completed = "Existing row updated in place." in current[latest.end():] or "Could not update this existing row." in current[latest.end():]
        verb = "Checked" if completed else "Checking"
        return f"{verb} {latest.group(1)} of {latest.group(2)}: {latest.group(3)[:70]}"
    found = re.findall(r"Found (\d+) existing results to verify", current)
    if found:
        return f"Found {found[-1]} existing results to check"
    return "Preparing existing results"


def search_progress_from_log(mode):
    fallback = "Starting replacement search" if mode == "replacement" else "Starting search"
    if not webfiles.SCRAPER_LOG_FILE.exists():
        return {"progress": fallback, "passed": 0, "checked": 0, "saved": 0, "limit": None}
    try:
        with webfiles.SCRAPER_LOG_FILE.open("rb") as log_file:
            size = log_file.seek(0, 2)
            log_file.seek(max(0, size - 2097152))
            tail = log_file.read().decode("utf-8", errors="replace")
    except OSError:
        return {"progress": fallback, "passed": 0, "checked": 0, "saved": 0, "limit": None}
    markers = list(re.finditer(r"\| " + re.escape(mode) + r" \|[^\r\n]*===", tail))
    if not markers:
        return {"progress": fallback, "passed": 0, "checked": 0, "saved": 0, "limit": None}
    current = tail[markers[-1].end():]
    candidates = list(re.finditer(r"Checking result (\d+): ([^\r\n]+)", current))
    passed_matches = list(re.finditer(r"Passed validation: (\d+)", current))
    passed = int(passed_matches[-1].group(1)) if passed_matches else 0
    # "Passed" counts every job that passed, including ones already in the list; only new saves count toward the limit.
    saved_matches = list(re.finditer(r"Saved (?:job |viable company \()(\d+)/(\d+)", current))
    saved = int(saved_matches[-1].group(1)) if saved_matches else 0
    limit = int(saved_matches[-1].group(2)) if saved_matches else None
    events = list(re.finditer(r"^(Checking result \d+: |Skipped \([^\r\n]+?\): |Passed validation: \d+ · |Saved lead for review: )([^\r\n]+)", current, re.M))
    if events:
        latest = events[-1]
        progress = (latest.group(1) + latest.group(2))[:220]
    else:
        progress = "Searching for results"
    checked = int(candidates[-1].group(1)) if candidates else 0
    return {"progress": progress, "passed": passed, "checked": checked, "saved": saved, "limit": limit}


@app.route("/search-status")
def search_status():
    running, mode = scraper_status()
    elapsed = int(time.monotonic() - scraper_started_at) if running and scraper_started_at else 0
    activity = search_progress_from_log(mode) if running and mode in ("search", "replacement") else None
    stop_reason = None
    if not running and webfiles.SCRAPER_LOG_FILE.exists():
        try:
            with webfiles.SCRAPER_LOG_FILE.open("rb") as log_file:
                size = log_file.seek(0, 2)
                log_file.seek(max(0, size - 8192))
                reasons = re.findall(r"Stop reason: ([^\r\n]+)", log_file.read().decode("utf-8", errors="replace"))
                stop_reason = reasons[-1] if reasons else None
        except OSError:
            pass
    return jsonify({
        "running": running,
        "mode": mode,
        "stopping": running and scraper_stopping,
        "error": scraper_last_error,
        "progress": (update_progress_from_log() if mode in ("update", "refresh") else import_progress_from_log() if mode == "import"
                     else activity["progress"] if activity else None) if running else None,
        "passed": activity["passed"] if activity else None,
        "saved": activity["saved"] if activity else None,
        "limit": activity["limit"] if activity else None,
        "checked": activity["checked"] if activity else None,
        "elapsed_seconds": elapsed,
        "stop_reason": stop_reason,
    })


if __name__ == "__main__":
    initialize_database()
    ensure_keep_column()
    schema.ensure_job_tracking_columns()
    ensure_profile_tables()
    tidy_expired_closed_jobs()
    app.run(host="127.0.0.1", port=5000, debug=False, use_reloader=False)
