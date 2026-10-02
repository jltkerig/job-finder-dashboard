"""Settings, constants and the state every part of the search shares. Other modules read the settings that tests change (REQUEST_DELAY, MAX_SEARCH_RESULTS, update_existing_mode ...) as config.NAME, so changing one here changes it everywhere."""

import json
import re
import threading
import time

from dotenv import load_dotenv

from jobfinder import paths
from jobfinder.records.board_health import BoardHealth
from jobfinder.records.search_skips import record_decision

# The database login and other private settings live in .env (read once, here, for every module of the search).
load_dotenv(paths.ENV_FILE)


BASE_DIR = paths.ROOT


SETTINGS_FILE = paths.SETTINGS_FILE


BLOCKED_DOMAINS_FILE = paths.BLOCKED_DOMAINS_FILE


BLOCKED_COMPANIES_FILE = paths.BLOCKED_COMPANIES_FILE


BLOCKED_COUNTRY_DOMAINS_FILE = paths.BLOCKED_COUNTRY_DOMAINS_FILE


SEARCH_SKIPS_FILE = paths.SEARCH_SKIPS_FILE


# The dashboard creates this file to ask a running search to stop and clean up.
STOP_REQUEST_FILE = paths.STOP_REQUEST_FILE


# Rolling record of the last few runs (inputs and why each lead was kept or skipped).
SEARCH_DEBUG_FILE = paths.SEARCH_DEBUG_FILE


_debug_run = None


_skip_decisions = {}


def stop_requested():
    return STOP_REQUEST_FILE.exists()


def debug_skip(reason, url, title=""):
    """Record a skip in the debug file only (for paths that are not cached as skips)."""
    if _debug_run is not None:
        _debug_run.skip(reason, url, title)


def debug_lead(**fields):
    """Record why a lead was kept (or updated) in the debug file."""
    if _debug_run is not None:
        _debug_run.lead(**fields)


def record_skip(reason, url, title=""):
    """Keep each search decision for review and short-lived cache checks."""
    print(f"Skipped ({reason}): {title[:70]} {url[:120]}", flush=True)
    record_decision(SEARCH_SKIPS_FILE, _skip_decisions, reason, url, title)
    debug_skip(reason, url, title)


SEARXNG_COMPOSE_FILE = BASE_DIR / "searxng" / "docker-compose.yml"


def load_settings():
    try:
        with SETTINGS_FILE.open(
            "r",
            encoding="utf-8",
        ) as file:
            return json.load(file)

    except FileNotFoundError:
        print("settings.json was not found.")
        raise

    except json.JSONDecodeError as error:
        print("There is a problem in settings.json.")
        print(error)
        raise


def load_domain_file(path):
    if not path.exists():
        print(f"Warning: {path.name} was not found.")
        return set()

    with path.open(
        "r",
        encoding="utf-8",
    ) as file:
        return {
            line.strip().lower()
            for line in file
            if line.strip() and not line.strip().startswith("#")
        }


settings = load_settings()


BLOCKED_DOMAINS = load_domain_file(BLOCKED_DOMAINS_FILE)


BLOCKED_COMPANIES = load_domain_file(BLOCKED_COMPANIES_FILE)


BLOCKED_COUNTRY_DOMAINS = load_domain_file(BLOCKED_COUNTRY_DOMAINS_FILE)


SEARXNG_URL = "http://localhost:8080/search"


SEARXNG_CONTAINER = "searxng"


SEARXNG_MAX_RUNTIME = settings["searxng_timeout_minutes"] * 60


MAX_SEARCH_RESULTS = settings["max_search_results"]


MAX_SEARCH_PAGES = settings.get("max_search_pages", 20)


REQUEST_DELAY = settings["request_delay_seconds"]


# Pause between search-engine requests, and how many empty queries in a row mean the engines are blocking us.
QUERY_DELAY = settings.get("search_query_delay_seconds", 2)


PREFETCH_WORKERS = settings.get("parallel_page_fetches", 6)


EMPTY_QUERY_LIMIT = settings.get("stop_after_empty_queries", 8)


TIMEOUT = settings["website_timeout_seconds"]


START_DOCKER_AUTOMATICALLY = settings.get(
    "start_docker_automatically",
    True,
)


STOP_DOCKER_WHEN_FINISHED = settings.get(
    "stop_docker_when_finished",
    True,
)


USA_ONLY = settings.get(
    "usa_only",
    True,
)


# Internships and co-ops are skipped unless "exclude_internships" is switched off on the Tuning page.
EXCLUDE_INTERNSHIPS = settings.get("exclude_internships", True)


# Closely related job titles that are matched (not searched for by name), so "Multimedia Designer" is not missed
# just because it was not typed. Switch off with "related_titles": false.
RELATED_TITLES = settings.get("related_titles", True)


_TITLE_FAMILIES = (
    (re.compile(r"\bdesign(?:er)?\b", re.I), ("Multimedia Designer", "Brand Designer", "Creative Designer", "Marketing Designer",
                                                "Email Designer", "Communications Designer")),
    (re.compile(r"\bproduction\b", re.I), ("Production Artist", "Web Production Specialist", "Digital Production Specialist")),
    (re.compile(r"\bproducer\b", re.I), ("Website Producer", "Digital Producer")),
    (re.compile(r"\bcontent designer\b", re.I), ("UX Writer",)),
)


def related_family_titles(typed):
    """Extra titles to match, from the families the typed titles belong to; never a title already covered."""
    if not RELATED_TITLES:
        return []
    have = {title.casefold() for title in typed}
    extras = []
    for pattern, titles in _TITLE_FAMILIES:
        if any(pattern.search(title) for title in typed):
            for title in titles:
                if title.casefold() not in have and title not in extras:
                    extras.append(title)
    return extras


# Job-board sites searched directly (one query per title). Each company board found there is then read in full
# through its public API. Add more, such as "boards.greenhouse.io", in settings.json under "job_board_sites".
JOB_BOARD_SITES = settings.get("job_board_sites", ["jobs.ashbyhq.com", "greenhouse.io", "jobs.lever.co", "apply.workable.com",
                                                       "jobs.smartrecruiters.com"])


# Board-site queries return many companies and few pages matter, so they read fewer result pages than title queries.
SITE_QUERY_PAGES = settings.get("site_query_pages", 2)


_INTERNSHIP = re.compile(r"\b(?:intern|interns|internship|internships|co-?op)\b", re.I)


def is_internship(title, schedule=""):
    """True for an internship or co-op: the title says so, or the listing's work type is 'intern'."""
    return bool(EXCLUDE_INTERNSHIPS and (_INTERNSHIP.search(str(title or ""))
                                         or re.search(r"\bintern", str(schedule or ""), re.I)))


USER_AGENT = "PersonalJobFinder/1.0"


MAX_HTML_SIZE = 2_000_000


_page_cache = {}


_prefetched = {}  # Update/Refresh: pages downloaded ahead of the row that needs them (used once)


_timings = {}


_board_health = BoardHealth()


class timed:
    """Adds the time spent inside a with-block to _timings[name] (safe to use from several threads)."""

    def __init__(self, name):
        self.name = name

    def __enter__(self):
        self.started = time.monotonic()

    def __exit__(self, *exc):
        with _host_lock:
            _timings[self.name] = _timings.get(self.name, 0.0) + time.monotonic() - self.started


def timing_summary():
    return {name: round(seconds, 1) for name, seconds in _timings.items()}


_host_lock = threading.Lock()


_host_next_request = {}


_last_failure_status = None


HEADERS = {"User-Agent": USER_AGENT}


CAREER_CREDIBILITY_THRESHOLD = 3


USA_CREDIBILITY_THRESHOLD = 5


MAX_DISCOVERY_PAGES = 8


update_existing_mode = False
