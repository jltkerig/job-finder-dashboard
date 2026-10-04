"""Paths and settings shared by the web app and the Claude Desktop connector."""
import os
from pathlib import Path

from dotenv import load_dotenv

APP_VERSION = "0.1.0"
APP_DIR = Path(__file__).resolve().parent
JOB_FINDER_DIR = Path(os.getenv("JOB_FINDER_DIR", APP_DIR.parent)).resolve()

# Our own .env wins; Job Finder's .env supplies the database login so it lives in one place.
load_dotenv(APP_DIR / ".env")
load_dotenv(JOB_FINDER_DIR / ".env")

DB_HOST = os.getenv("DB_HOST", "127.0.0.1")
DB_PORT = int(os.getenv("DB_PORT") or 3306)
DB_USER = os.getenv("DB_USER", "root")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")
DB_NAME = os.getenv("DB_NAME", "job_finder")

# Finished PDFs go next to Job Finder so they sit with the rest of the job search.
OUTPUT_DIR = Path(os.getenv("RESUME_OUTPUT_DIR", JOB_FINDER_DIR / "user-builds")).resolve()
DATA_DIR = Path(os.getenv("RESUME_DATA_DIR", APP_DIR / "data")).resolve()
UPLOAD_DIR = DATA_DIR / "current-resume"
DRAFTS_DIR = DATA_DIR / "drafts"
FONT_DIR = DATA_DIR / "fonts"  # Google Fonts downloaded when a design uses them

PORT = int(os.getenv("RESUME_BUILDER_PORT") or 5001)
JOB_FINDER_URL = os.getenv("JOB_FINDER_URL", "http://127.0.0.1:5000")
MAX_UPLOAD_BYTES = 10 * 1024 * 1024


def ensure_dirs():
    for path in (OUTPUT_DIR, UPLOAD_DIR, DRAFTS_DIR):
        path.mkdir(parents=True, exist_ok=True)
