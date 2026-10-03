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
from jobfinder.web.listing_queries import (  # noqa: F401  (also used by callers that import these from here)
    add_drive_times,
    add_job_fit,
    get_companies,
    get_dashboard_counts,
    get_kept_companies,
)
from jobfinder.web import listing_queries
from jobfinder.web.tuning import (  # noqa: F401  (also used by callers that import these from here)
    TUNING_FIELDS,
    TUNING_SWITCHES,
    apply_tuning_form,
    read_tuning_settings,
    save_tuning_settings,
    tuning_page,
)
from jobfinder.web.update_checks import (  # noqa: F401  (also used by callers that import these from here)
    app_version,
    check_update,
    find_latest_update_zip,
    install_update,
)
from jobfinder.web.extension_api import (  # noqa: F401  (also used by callers that import these from here)
    extension_distances,
    extension_file,
    extension_fit_profile,
    extension_updates,
    latest_extension_build,
)
from jobfinder.web.search_control import (  # noqa: F401  (also used by callers that import these from here)
    _finish_graceful_stop,
    _home_state,
    _home_state_cache,
    captures_import,
    captures_pending,
    import_progress_from_log,
    launch_search_process,
    refresh_search,
    replace_result,
    scraper_is_running,
    scraper_last_error,
    scraper_lock,
    scraper_mode,
    scraper_process,
    scraper_started_at,
    scraper_status,
    scraper_stopping,
    search_progress_from_log,
    search_status,
    start_search,
    stop_search,
    update_existing,
    update_progress_from_log,
    validate_search_criteria,
)
from jobfinder.web.listing_actions import (  # noqa: F401  (also used by callers that import these from here)
    block_domain,
    delete_kept,
    reject_listing,
    restore_rejected,
    save_kept,
    unsave_kept,
    update_kept,
)
from jobfinder.web.profile_routes import (  # noqa: F401  (also used by callers that import these from here)
    city_matches_route,
    job_title_matches,
    job_title_suggestions,
    parse_resume,
    save_profile,
    save_profile_title,
)


APP_VERSION = "1.1.150"

app.config.update(APP_VERSION=APP_VERSION)


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
        companies=listing_queries.get_rejected_companies(),
        search_skips=search_skips, skip_total=skip_total, skip_page=skip_page, skip_pages=skip_pages, skip_query=skip_query,
        blocked_domains=blocklists.get_blocked_domains(),
        blocked_companies=blocklists.get_blocked_companies(),
        blocked_domain_info=block_details("domains", blocklists.get_blocked_domains()),
        blocked_company_info=block_details("companies", blocklists.get_blocked_companies()),
        company_notice=request.args.get("company_notice", ""),
        domain_notice=request.args.get("domain_notice", ""),
    )


if __name__ == "__main__":
    initialize_database()
    ensure_keep_column()
    schema.ensure_job_tracking_columns()
    ensure_profile_tables()
    tidy_expired_closed_jobs()
    app.run(host="127.0.0.1", port=5000, debug=False, use_reloader=False)
