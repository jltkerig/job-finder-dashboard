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
from jobfinder.web.pages import (  # noqa: F401  (also used by callers that import these from here)
    credibility_scores,
    home,
    rejected_listings,
    user_dashboard,
)


APP_VERSION = "1.1.150"

app.config.update(APP_VERSION=APP_VERSION)


if __name__ == "__main__":
    initialize_database()
    ensure_keep_column()
    schema.ensure_job_tracking_columns()
    ensure_profile_tables()
    tidy_expired_closed_jobs()
    app.run(host="127.0.0.1", port=5000, debug=False, use_reloader=False)
