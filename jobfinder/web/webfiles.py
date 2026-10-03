"""The files, folders and fixed settings the web pages use. Other modules read these as webfiles.NAME so a test can change one in one place."""

from pathlib import Path
import os
import re

from jobfinder import paths
from zoneinfo import ZoneInfo


BASE_DIR = paths.ROOT


# Where the Resume Builder keeps the résumé you uploaded there (its text is read to suggest skills).
RESUME_FOLDER = Path(os.getenv("RESUME_BUILDER_DIR", BASE_DIR.parent / "resume-builder")) / "data" / "current-resume"


JOB_FINDER_PATH = BASE_DIR / "job_finder.py"


BLOCKED_DOMAINS_FILE = paths.BLOCKED_DOMAINS_FILE


BLOCKED_COMPANIES_FILE = paths.BLOCKED_COMPANIES_FILE


BLOCK_METADATA_FILE = paths.BLOCK_METADATA_FILE


RECOMMENDED_DOMAINS_FILE = paths.RECOMMENDED_DOMAINS_FILE


SCRAPER_LOG_FILE = paths.SEARCH_LOG


SEARCH_SKIPS_FILE = paths.SEARCH_SKIPS_FILE


UPDATE_SCRIPT = paths.UPDATE_SCRIPT


UPDATE_MARKER = paths.UPDATE_MARKER


# Update ZIPs are named job-finder-dashboard-vX.Y.Z.zip; older ones (job-finder-vX.Y.Z.zip) still count.
UPDATE_ZIP_PATTERN = re.compile(r"^job-finder(?:-dashboard)?-v(\d+)\.(\d+)\.(\d+)\.zip$", re.IGNORECASE)


UPDATE_SEARCH_DIRS = [
    Path.home() / "Downloads",
    BASE_DIR.parent,
]


# job_finder.py watches for this file and shuts down cleanly when it appears.
STOP_REQUEST_FILE = paths.STOP_REQUEST_FILE


SETTINGS_FILE = paths.SETTINGS_FILE


GRACEFUL_STOP_SECONDS = 90


# Choices for a saved job's Application Status, in the order the dropdowns show them.
APPLICATION_STATUSES = ["None", "Saved", "Applied", "Talking With Recruiter", "Interview", "Rejected", "Closed"]


LOCAL_HOSTS = {"127.0.0.1", "localhost", "[::1]"}


COMPRESSIBLE = {"text/html", "text/css", "application/javascript", "text/javascript", "application/json", "image/svg+xml"}


EXTENSION_ID = "web-job-scraper@jamie.local"


EXTENSION_FILE = re.compile(r"^web-job-scraper-v(\d+)\.(\d+)\.(\d+)\.xpi$")


LOCAL_TIMEZONE = ZoneInfo("America/New_York")


SKIPS_PER_PAGE = 25
