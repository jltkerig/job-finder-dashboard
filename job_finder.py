import argparse
import json
import math
import time
import os
import re
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urljoin, urlparse

import mysql.connector
from jobfinder import db, paths
import requests
from bs4 import BeautifulSoup
from mysql.connector import Error
from dotenv import load_dotenv
from jobfinder.profiles.profile_tools import listing_skills, refresh_listing_skills
from jobfinder.sources.employer_jobs import (SOURCE_TYPE as EMPLOYER_SOURCE, Http as EmployerHttp, Employer, config_key, employer_for_url,
                           load_employers, record_board_result, save_discovered)
from jobfinder.sources.ats_discovery import identify as identify_board, identify_unreadable, pretty_name as board_name
from jobfinder.sources.job_feeds import FEEDS, FEED_NAMES
from jobfinder.sources.job_listings import arrangement_types, canonical_url, extract_jobs, is_article_page, is_pdf_url, job_links, pagination_links, excludes_us, matching_title as matching_job_title
from jobfinder.sources.ats_feeds import public_board_links
from jobfinder.profiles.onet_data import related_title_suggestions, spelling_fix
from jobfinder.records.search_skips import cached_skip, latest_decisions, record_decision
from jobfinder.sources.closed_jobs import listing_closed
from jobfinder.db_schema import ensure_unique_source_index
from jobfinder.sources.employer_site import clear_cache as clear_employer_cache, is_third_party, resolve_employer_site
from jobfinder.records.search_debug import DebugRun
from jobfinder.sources.remote_states import restriction_states
from jobfinder.records.board_health import BoardHealth, HEALTH_FILE as BOARD_HEALTH_FILE
from jobfinder.sources.ats_lookup import clear_cache as clear_ats_cache, find_ats_posting
from jobfinder.records.capture_import import (CAPTURE_MARK, CAPTURE_SOURCES, IMPORT_ERRORS, SITES as CAPTURE_SITES, add_also_on,
                            capture_dirs, files_to_import, mark_imported, match_key, merge_details,
                            page_html as capture_page_html, read_jobs, remove_old_folders, unreadable_files)
from jobfinder.profiles.travel import miles_between
from jobfinder.records.job_retention import CLOSED_KEEP_DAYS, tidy_closed_jobs
from jobfinder.sources.job_sites import JOB_SITES, JOB_SITE_NAMES, SiteBlocked, job_site_for, search_places
from jobfinder.search.shared import (  # noqa: F401  (also used by callers that import these from here)
    BASE_DIR,
    BLOCKED_COMPANIES,
    BLOCKED_COMPANIES_FILE,
    BLOCKED_COUNTRY_DOMAINS,
    BLOCKED_COUNTRY_DOMAINS_FILE,
    BLOCKED_DOMAINS,
    BLOCKED_DOMAINS_FILE,
    CAREER_CREDIBILITY_THRESHOLD,
    EMPTY_QUERY_LIMIT,
    HEADERS,
    MAX_DISCOVERY_PAGES,
    MAX_HTML_SIZE,
    PREFETCH_WORKERS,
    SEARCH_SKIPS_FILE,
    SEARXNG_COMPOSE_FILE,
    SEARXNG_CONTAINER,
    SEARXNG_MAX_RUNTIME,
    SEARXNG_URL,
    SETTINGS_FILE,
    SITE_QUERY_PAGES,
    START_DOCKER_AUTOMATICALLY,
    STOP_DOCKER_WHEN_FINISHED,
    TIMEOUT,
    USA_ONLY,
    USER_AGENT,
    _INTERNSHIP,
    _TITLE_FAMILIES,
    _board_health,
    _host_lock,
    _host_next_request,
    _page_cache,
    _prefetched,
    _skip_decisions,
    _timings,
    debug_lead,
    debug_skip,
    is_internship,
    load_domain_file,
    load_settings,
    record_skip,
    related_family_titles,
    settings,
    timed,
    timing_summary,
)
from jobfinder.search import shared
from jobfinder.search.docker import (  # noqa: F401
    docker_engine_running,
    docker_started_by_program,
    run_command,
    run_docker_command,
    searxng_exists,
    searxng_is_running,
    searxng_start_time,
    stop_announced,
    wait_for_searxng,
)
from jobfinder.search import docker
from jobfinder.search.storage import (  # noqa: F401  (also used by callers that import these from here)
    DB_HOST,
    DB_NAME,
    DB_PASSWORD,
    DB_PORT,
    DB_USER,
    clear_companies_table,
)
from jobfinder.search import storage
from jobfinder.search.fetching import (  # noqa: F401  (also used by callers that import these from here)
    _fetch_page,
    _safe_request,
    get_domain,
    has_blocked_country_domain,
    is_blocked_domain,
    is_valid_url,
    prefetch_for_update,
    prefetch_pages,
    remember_page,
    wait_for_host,
)
from jobfinder.search import fetching
from jobfinder.search.company_names import (  # noqa: F401  (also used by callers that import these from here)
    OFFICIAL_BOARD_CREDIBILITY,
    _JOB_TITLE_WORDS,
    _LEADING_CODE,
    _ats_http,
    clean_company_name,
    company_board_posting,
    company_from_title,
    extract_company_name,
    find_json_ld_company_name,
    on_company_site,
    on_official_board,
    tidy_company_name,
    verification_label,
)
from jobfinder.search.usa_location import (  # noqa: F401  (also used by callers that import these from here)
    US_STATES,
    US_STATE_ABBREVIATIONS,
    _AMBIGUOUS_ABBREVIATIONS,
    _LOCATION_CUE,
    _OUT_OF_STATE,
    _PAGE_NOISE_ELEMENTS,
    _PAGE_NOISE_NAMES,
    _RESIDENCY_TRIGGER,
    _STATE_ABBREVIATION,
    _STATE_BOILERPLATE,
    _STATE_NAMES,
    _WORD_LIKE_STATES,
    analyze_usa_location,
    detect_work_arrangement,
    find_state_from_text,
    has_location_cue,
    inspect_json_ld,
    iter_json_ld_objects,
    merge_location_data,
    page_body_text,
    remote_state_restrictions,
    selected_state_codes,
)
from jobfinder.search.searching import (  # noqa: F401  (also used by callers that import these from here)
    _last_search_time,
    _search_searxng,
    last_search_health,
    search_blocked_message,
    search_searxng,
)
from jobfinder.search.company_site import (  # noqa: F401  (also used by callers that import these from here)
    ATS_DOMAINS,
    CAREER_NEGATIVE_TERMS,
    CAREER_STRONG_TERMS,
    CAREER_WEAK_TERMS,
    DIRECTORY_MARKETPLACE_DOMAINS,
    DIRECTORY_TITLE_PATTERNS,
    DIRECTORY_WEAK_TITLE,
    SOCIAL_DOMAINS,
    SUPPORT_PAGE_TERMS,
    discover_robots_links,
    discover_sitemap_links,
    discover_support_links,
    find_external_company_site,
    inspect_company_site,
    is_directory_or_marketplace_result,
    is_student_employment_overview,
    score_career_page,
)
from jobfinder.search.geo import (  # noqa: F401  (also used by callers that import these from here)
    JOB_FINDER_VERSION,
    NOMINATIM_URL,
    _PLACE_KIND_RANK,
    _REGION_ALIASES,
    _REGION_WORDS,
    _ZIP_CODE,
    _failed_geocode_queries,
    _last_nominatim_request,
    _normalize_geocode_query,
    _place_name,
    _read_app_version,
    clean_region_name,
    distance_to_city_targets,
    extract_job_city,
    geocode_location,
    geocode_queries,
    haversine_miles,
    pick_place,
    place_matches,
)
from jobfinder.search import geo
from jobfinder.search.relevance import (  # noqa: F401  (also used by callers that import these from here)
    apply_link_closed,
    expand_job_titles,
    fetch_text,
    is_excluded_employer_host,
    is_internship_row,
    is_irrelevant_lead,
    is_wrong_location_lead,
    load_city_targets,
    load_selected_states,
    load_user_titles,
    reject_irrelevant_row,
    stale_remote_limit,
)
from jobfinder.search import relevance
from jobfinder.search import judging
from jobfinder.search.web_captures import (  # noqa: F401  (also used by callers that import these from here)
    CaptureImport,
    _row_details,
    import_captures,
    site_location_in_us,
)
from jobfinder.search import web_captures
from jobfinder.search.refresh import (  # noqa: F401  (also used by callers that import these from here)
    RESTORABLE_REASONS,
    SEARCH_SCOPE_FILE,
    close_expired_listings,
    close_expired_listings_quietly,
    current_search_scope,
    location_matches_scope,
    recheck_system_rejections,
    recheck_system_rejections_quietly,
    refresh_job_fit_quietly,
    restore_rejected_row,
    update_existing_results,
)
from jobfinder.search.runner import (  # noqa: F401  (also used by callers that import these from here)
    _run_search,
    main,
    parse_arguments,
    web_search_started,
)

# =========================================================
# FILES
# =========================================================


# =========================================================
# LOAD SETTINGS
# =========================================================


# =========================================================
# PROGRAM SETTINGS
# =========================================================

