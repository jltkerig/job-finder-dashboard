"""Where every file lives. Nothing else in Job Finder builds a path from __file__, so the layout is decided here.

    <project>/
        dashboard.py, job_finder.py      the two programs you start (see README.md)
        jobfinder/                       the code
        templates/, static/              the web pages
        data/                            reference data that ships with Job Finder (O*NET, places, ZIP codes)
        data/defaults/                   the starting copy of each settings and block list
        user-data/                       YOURS: settings, block lists, watched employers, search records (kept on update)
        logs/                            dashboard and search logs
        user-builds/                     résumés and cover letters from Résumé Builder
        tests/                           the tests (python -m unittest discover -s tests -t tests)

Updates replace everything except user-data/, logs/ and user-builds/, so your own files are never overwritten.
"""
import os
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
DEFAULTS_DIR = DATA_DIR / "defaults"
# JOBFINDER_USER_DIR and JOBFINDER_LOG_DIR move them elsewhere (the tests use a temporary folder so they never touch yours).
USER_DIR = Path(os.environ.get("JOBFINDER_USER_DIR") or ROOT / "user-data")
LOG_DIR = Path(os.environ.get("JOBFINDER_LOG_DIR") or ROOT / "logs")
ENV_FILE = ROOT / ".env"

# Reference data shipped with Job Finder.
ONET_DIR = DATA_DIR / "onet-31.0"
PLACES_FILE = DATA_DIR / "us-places.tsv"
ZIPS_FILE = DATA_DIR / "us-zips.tsv"
RECOMMENDED_DOMAINS_FILE = DATA_DIR / "recommended_domains.txt"

# Your files. A missing one is created from data/defaults (or moved over from where older versions kept it).
SETTINGS_FILE = USER_DIR / "settings.json"
BLOCKED_DOMAINS_FILE = USER_DIR / "blocked_domains.txt"
BLOCKED_COMPANIES_FILE = USER_DIR / "blocked_companies.txt"
BLOCKED_COUNTRY_DOMAINS_FILE = USER_DIR / "blocked_country_domains.txt"
BLOCK_METADATA_FILE = USER_DIR / "block_metadata.json"
WATCHED_EMPLOYERS_FILE = USER_DIR / "watched_employers.json"
DISCOVERED_EMPLOYERS_FILE = USER_DIR / "discovered_employers.json"
BOARD_HEALTH_FILE = USER_DIR / "board_health.json"
SEARCH_SKIPS_FILE = USER_DIR / "search_skips.jsonl"
SEARCH_DEBUG_FILE = USER_DIR / "search_debug.json"
SEARCH_SCOPE_FILE = USER_DIR / "search_scope.json"
STOP_REQUEST_FILE = USER_DIR / ".stop-requested"
FEED_CACHE_DIR = USER_DIR / "feed-cache"

# Logs.
DASHBOARD_LOG = LOG_DIR / "dashboard.log"
DASHBOARD_ERROR_LOG = LOG_DIR / "dashboard-error.log"
SEARCH_LOG = LOG_DIR / "job_finder.log"
STARTUP_LOG = LOG_DIR / "startup.log"

# Files the updater writes at the top of the project.
UPDATE_SCRIPT = ROOT / "update.ps1"
UPDATE_MARKER = ROOT / ".update-in-progress"

# Files that older versions kept at the top of the project: (old name, new place).
_MOVED = [
    ("settings.json", SETTINGS_FILE), ("blocked_domains.txt", BLOCKED_DOMAINS_FILE),
    ("blocked_companies.txt", BLOCKED_COMPANIES_FILE), ("blocked_country_domains.txt", BLOCKED_COUNTRY_DOMAINS_FILE),
    ("block_metadata.json", BLOCK_METADATA_FILE), ("watched_employers.json", WATCHED_EMPLOYERS_FILE),
    ("discovered_employers.json", DISCOVERED_EMPLOYERS_FILE), ("board_health.json", BOARD_HEALTH_FILE),
    ("search_skips.jsonl", SEARCH_SKIPS_FILE), ("search_debug.json", SEARCH_DEBUG_FILE),
    ("search_scope.json", SEARCH_SCOPE_FILE), (".stop-requested", STOP_REQUEST_FILE), ("feed_cache", FEED_CACHE_DIR),
    ("dashboard.log", DASHBOARD_LOG), ("dashboard-error.log", DASHBOARD_ERROR_LOG), ("job_finder.log", SEARCH_LOG),
    ("startup.log", STARTUP_LOG),
]
_DEFAULTED = [SETTINGS_FILE, BLOCKED_DOMAINS_FILE, BLOCKED_COMPANIES_FILE, BLOCKED_COUNTRY_DOMAINS_FILE, WATCHED_EMPLOYERS_FILE]


def ensure_user_files(root=None):
    """Make the folders, move files from their old places, and start any missing list from its default.

    Safe to call again and again (every start does). Nothing that already exists in user-data/ is overwritten.
    `root` is only for tests: a folder laid out like the project.
    """
    base = Path(root) if root else ROOT
    user = base / "user-data" if root else USER_DIR
    logs = base / "logs" if root else LOG_DIR
    user.mkdir(parents=True, exist_ok=True)
    logs.mkdir(parents=True, exist_ok=True)
    for old_name, new in _MOVED:
        old = base / old_name
        target = (logs / new.name) if new.parent == LOG_DIR else (user / new.relative_to(USER_DIR))
        if old.exists() and not target.exists():
            try:
                shutil.move(str(old), str(target))
            except OSError:
                pass  # a log that is open right now stays where it is; the new one starts fresh
    for new in _DEFAULTED:
        target = user / new.relative_to(USER_DIR)
        default = base / "data" / "defaults" / new.name
        if not target.exists() and default.exists():
            shutil.copyfile(default, target)


if not os.environ.get("JOBFINDER_NO_SETUP"):
    ensure_user_files()
