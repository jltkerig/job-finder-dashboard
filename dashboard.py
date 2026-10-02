from datetime import datetime, timedelta, timezone
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
from dotenv import load_dotenv
from flask import Flask, abort, jsonify, redirect, render_template, request, send_from_directory, session
from mysql.connector import Error
from profile_tools import AMBIGUOUS_SKILLS, SKILL_ALIASES, fit_score, normalize_skills, resume_suggestions
from onet_data import occupation_skill_suggestions, related_title_suggestions, spelling_fix, title_matches
from places import city_matches
from travel import describe as describe_trip
from job_listings import NON_JOB_PATH, is_pdf_url
from db_schema import ensure_unique_source_index
from job_feeds import FEED_NAMES
from job_sites import JOB_SITE_NAMES
from board_health import STATUSES as HEALTH_STATUSES, read_health
from search_skips import TTL_HOURS, latest_decisions
from capture_import import CAPTURE_SOURCES, capture_dirs, move_pending, pending_files
from job_retention import tidy_closed_jobs

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

app = Flask(__name__)

APP_VERSION = "1.1.123"

app.config["SECRET_KEY"] = os.getenv("FLASK_SECRET_KEY") or secrets.token_hex(32)
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Strict"

DB_HOST = os.getenv("DB_HOST", "127.0.0.1")
DB_PORT = int(os.getenv("DB_PORT", "3306"))
DB_USER = os.getenv("DB_USER", "root")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")
DB_NAME = os.getenv("DB_NAME", "job_finder")

JOB_FINDER_PATH = BASE_DIR / "job_finder.py"
BLOCKED_DOMAINS_FILE = BASE_DIR / "blocked_domains.txt"
BLOCKED_COMPANIES_FILE = BASE_DIR / "blocked_companies.txt"
BLOCK_METADATA_FILE = BASE_DIR / "block_metadata.json"
RECOMMENDED_DOMAINS_FILE = BASE_DIR / "recommended_domains.txt"
SCRAPER_LOG_FILE = BASE_DIR / "job_finder.log"
SEARCH_SKIPS_FILE = BASE_DIR / "search_skips.jsonl"
UPDATE_SCRIPT = BASE_DIR / "update.ps1"
UPDATE_MARKER = BASE_DIR / ".update-in-progress"
# Update ZIPs are named job-finder-dashboard-vX.Y.Z.zip; older ones (job-finder-vX.Y.Z.zip) still count.
UPDATE_ZIP_PATTERN = re.compile(r"^job-finder(?:-dashboard)?-v(\d+)\.(\d+)\.(\d+)\.zip$", re.IGNORECASE)
UPDATE_SEARCH_DIRS = [
    Path.home() / "Downloads",
    BASE_DIR.parent,
]
scraper_process = None
scraper_mode = None
scraper_last_error = None
scraper_lock = threading.Lock()
scraper_started_at = None
scraper_stopping = False
# job_finder.py watches for this file and shuts down cleanly when it appears.
STOP_REQUEST_FILE = BASE_DIR / ".stop-requested"
SETTINGS_FILE = BASE_DIR / "settings.json"
GRACEFUL_STOP_SECONDS = 90
# Choices for a saved job's Application Status, in the order the dropdowns show them.
APPLICATION_STATUSES = ["None", "Saved", "Applied", "Talking With Recruiter", "Interview", "Rejected", "Closed"]
# Schema checks that already succeeded in this process; they only need to run once.
_schema_ready = set()


ERROR_CODES = {
    "csrf": "E2201",
    "bad_request": "E2001",
    "not_found": "E2002",
    "database": "E3001",
    "process": "E2003",
    "unexpected": "E9001",
}


def log_error_code(code, message):
    print(f"[{code}] {message}")


def api_error(code, message, status=400):
    log_error_code(code, message)
    return jsonify({"status": "error", "error_code": code, "message": message}), status


@app.errorhandler(403)
def handle_forbidden(error):
    if request.path.startswith(("/start-search", "/replace-result", "/refresh-search", "/update-existing", "/save-kept", "/search-status", "/reject-listing", "/restore-rejected", "/block-domain", "/unsave-kept", "/stop-search", "/install-update", "/captures/")):
        return api_error("E2201", "Your dashboard session expired. The page will refresh automatically.", 403)
    return error


@app.errorhandler(500)
def handle_internal_error(error):
    log_error_code("E9001", f"Unhandled server error on {request.path}: {error}")
    if request.path.startswith(("/start-search", "/replace-result", "/refresh-search", "/update-existing", "/save-kept", "/search-status", "/reject-listing", "/restore-rejected", "/block-domain", "/unsave-kept", "/stop-search", "/install-update", "/job-title-suggestions")):
        return jsonify({"status": "error", "error_code": "E9001", "message": "The dashboard hit an unexpected server error."}), 500
    return "Internal server error [E9001]", 500


API_PATH_CODES = {
    "/start-search": "E2101",
    "/replace-result": "E2110",
    "/refresh-search": "E2102",
    "/update-existing": "E2103",
    "/save-kept": "E3101",
    "/search-status": "E2104",
    "/job-title-suggestions": "E1101",
    "/unsave-kept": "E3102",
    "/stop-search": "E2105",
    "/captures/pending": "E2120",
    "/captures/import": "E2121",
}


@app.after_request
def attach_error_codes(response):
    if response.status_code < 400:
        return response

    code = API_PATH_CODES.get(request.path)
    if not code or not response.is_json:
        return response

    payload = response.get_json(silent=True) or {}
    if "error_code" not in payload:
        payload["error_code"] = code
        response.set_data(app.json.dumps(payload))
        response.mimetype = "application/json"
        log_error_code(code, f"{request.method} {request.path} -> HTTP {response.status_code}: {payload.get('message', 'Request failed')}")
    return response


RELATED_JOB_TITLES = {
    "web designer": ["UI Designer", "UX/UI Designer", "Digital Designer", "Website Designer", "Visual Designer", "WordPress Designer"],
    "front end developer": ["Frontend Developer", "Web Developer", "UI Developer", "Junior Web Developer", "WordPress Developer", "Web Content Developer"],
    "frontend developer": ["Front End Developer", "Web Developer", "UI Developer", "Junior Web Developer", "WordPress Developer", "Web Content Developer"],
    "web developer": ["Front End Developer", "Frontend Developer", "Junior Web Developer", "WordPress Developer", "UI Developer", "Web Application Developer"],
    "wordpress developer": ["Web Developer", "Front End Developer", "WordPress Designer", "Website Developer", "PHP Developer", "Web Content Developer"],
    "ui designer": ["Web Designer", "UX/UI Designer", "Visual Designer", "Product Designer", "Digital Designer", "Interaction Designer"],
    "ux designer": ["UX/UI Designer", "UI Designer", "Product Designer", "Interaction Designer", "Web Designer", "Experience Designer"],
    "ux/ui designer": ["UI Designer", "UX Designer", "Product Designer", "Web Designer", "Interaction Designer", "Digital Designer"],
    "graphic designer": ["Digital Designer", "Visual Designer", "Web Designer", "Production Designer", "Marketing Designer", "Brand Designer"],
    "website content coordinator": ["Web Content Coordinator", "Web Content Specialist", "Content Coordinator", "CMS Specialist", "Digital Content Specialist", "Website Coordinator"],
    "web content coordinator": ["Website Content Coordinator", "Web Content Specialist", "Content Coordinator", "CMS Specialist", "Digital Content Specialist", "Website Coordinator"],
    "content coordinator": ["Web Content Coordinator", "Website Content Coordinator", "Content Specialist", "Digital Content Specialist", "CMS Specialist", "Marketing Coordinator"],
}


def related_job_title_suggestions(raw_titles):
    selected = [part.strip() for part in (raw_titles or "").split(",") if part.strip()]
    selected_lower = {title.lower() for title in selected}
    suggestions = []

    for title in selected:
        key = re.sub(r"\s+", " ", title.lower()).strip()
        candidates = RELATED_JOB_TITLES.get(key, []) + related_title_suggestions(title)

        if not candidates:
            if "designer" in key:
                candidates = ["Web Designer", "UI Designer", "UX/UI Designer", "Digital Designer", "Visual Designer"]
            elif "developer" in key:
                candidates = ["Web Developer", "Front End Developer", "Frontend Developer", "UI Developer", "WordPress Developer"]
            elif "content" in key:
                candidates = ["Web Content Specialist", "Content Coordinator", "Digital Content Specialist", "CMS Specialist", "Website Coordinator"]

        for candidate in candidates:
            candidate_key = candidate.lower()
            if candidate_key not in selected_lower and candidate_key not in {item.lower() for item in suggestions}:
                suggestions.append(candidate)

    return suggestions[:8]


def profile_skill_suggestions(profile):
    selected = profile.get("primary_job_title") or next(iter(profile.get("job_titles") or []), "")
    # Use occupation examples to rank skills our listing parser can also recognize.
    aliases = {alias.casefold(): name for name, variants in SKILL_ALIASES.items()
               for alias in [name, *variants]}
    ranked = []
    for example in occupation_skill_suggestions(selected, limit=150):
        lower = example.casefold()
        canonical = aliases.get(lower)
        if canonical is None:
            canonical = next((name for alias, name in aliases.items()
                              if len(alias) >= 3 and re.search(r"(?<!\w)" + re.escape(alias) + r"(?!\w)", lower)), None)
        if canonical and canonical not in ranked:
            ranked.append(canonical)
    saved = {skill.casefold() for skill in profile.get("skills", [])}
    return [skill for skill in dict.fromkeys(ranked + list(SKILL_ALIASES))
            if skill.casefold() not in saved]




def _safe_db_identifier(value):
    if not re.fullmatch(r"[A-Za-z0-9_]+", value or ""):
        raise ValueError("DB_NAME may contain only letters, numbers, and underscores.")
    return value


def initialize_database():
    """Create only missing database objects. Never drops or overwrites existing data."""
    database_name = _safe_db_identifier(DB_NAME)
    connection = None
    cursor = None
    try:
        connection = mysql.connector.connect(
            host=DB_HOST, port=DB_PORT, user=DB_USER, password=DB_PASSWORD
        )
        cursor = connection.cursor()
        cursor.execute(f"CREATE DATABASE IF NOT EXISTS `{database_name}`")
        cursor.execute(f"USE `{database_name}`")
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS companies (
                id INT AUTO_INCREMENT PRIMARY KEY,
                name VARCHAR(255) NULL,
                career_job_title VARCHAR(255) NULL,
                career_credibility INT NULL,
                domain VARCHAR(255) NULL,
                career_url TEXT NULL,
                source_url TEXT NULL,
                source_type VARCHAR(50) NOT NULL DEFAULT 'SearXNG',
                country VARCHAR(100) NULL,
                state VARCHAR(100) NULL,
                city VARCHAR(150) NULL,
                latitude DECIMAL(10,7) NULL,
                longitude DECIMAL(10,7) NULL,
                distance_miles DECIMAL(8,2) NULL,
                work_arrangement VARCHAR(20) NULL,
                listing_skills TEXT NULL,
                listing_details TEXT NULL,
                usa_credibility INT NULL,
                date_found TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                last_checked TIMESTAMP NULL DEFAULT NULL,
                result_updated_at TIMESTAMP NULL DEFAULT NULL,
                is_kept TINYINT(1) NOT NULL DEFAULT 0,
                job_open_status VARCHAR(20) NOT NULL DEFAULT 'Open',
                application_status VARCHAR(30) NOT NULL DEFAULT 'None',
                notes TEXT NULL,
                is_rejected TINYINT(1) NOT NULL DEFAULT 0,
                rejection_reason VARCHAR(40) NULL,
                rejected_at TIMESTAMP NULL DEFAULT NULL,
                pre_reject_kept TINYINT(1) NULL,
                pre_reject_status VARCHAR(30) NULL
            )
        """)
        connection.commit()
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None and connection.is_connected():
            connection.close()


def get_csrf_token():
    token = session.get("csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["csrf_token"] = token
    return token


@app.context_processor
def inject_csrf_token():
    # Job sites (National Labor Exchange) list many employers' jobs like the remote feeds: shown as job-board
    # postings, and their domain is never offered for blocking.
    return {"csrf_token": get_csrf_token(), "app_version": APP_VERSION, "feed_names": sorted(FEED_NAMES | JOB_SITE_NAMES),
            "capture_sources": sorted(CAPTURE_SOURCES), "application_statuses": APPLICATION_STATUSES}


LOCAL_HOSTS = {"127.0.0.1", "localhost", "[::1]"}


@app.before_request
def refuse_foreign_hosts():
    """Answer only requests addressed to this computer. A web page can point its own name at 127.0.0.1 ("DNS
    rebinding") and then read the profile and jobs from here; such a request carries that page's name as the Host."""
    host = (request.host or "").rsplit(":", 1)[0] if not request.host.startswith("[") else request.host.split("]")[0] + "]"
    if host.lower() not in LOCAL_HOSTS:
        abort(400)
    return None


@app.before_request
def protect_local_post_requests():
    if request.method != "POST":
        return None

    supplied = request.headers.get("X-CSRF-Token") or request.form.get("csrf_token")
    expected = session.get("csrf_token")
    if not supplied or not expected or not secrets.compare_digest(supplied, expected):
        abort(403)

    return None


def ensure_keep_column():
    if "keep" in _schema_ready:
        return
    connection = None
    cursor = None

    try:
        connection = mysql.connector.connect(
            host=DB_HOST,
            port=DB_PORT,
            user=DB_USER,
            password=DB_PASSWORD,
            database=DB_NAME,
        )
        cursor = connection.cursor()
        cursor.execute("""
            ALTER TABLE companies
            ADD COLUMN IF NOT EXISTS is_kept TINYINT(1) NOT NULL DEFAULT 0
        """)
        connection.commit()
        _schema_ready.add("keep")
    except Error as error:
        print()
        print("Could not ensure the keep column exists.")
        print(error)
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None and connection.is_connected():
            connection.close()




def ensure_job_tracking_columns():
    if "tracking" in _schema_ready:
        return
    connection = None
    cursor = None

    try:
        connection = mysql.connector.connect(
            host=DB_HOST,
            port=DB_PORT,
            user=DB_USER,
            password=DB_PASSWORD,
            database=DB_NAME,
        )
        cursor = connection.cursor()
        for legacy, current in (("career_confidence", "career_credibility"),
                                ("usa_confidence", "usa_credibility")):
            cursor.execute("SHOW COLUMNS FROM companies LIKE %s", (legacy,))
            old_exists = cursor.fetchone() is not None
            cursor.execute("SHOW COLUMNS FROM companies LIKE %s", (current,))
            new_exists = cursor.fetchone() is not None
            if old_exists and not new_exists:
                cursor.execute(f"ALTER TABLE companies CHANGE COLUMN {legacy} {current} INT NULL")
            elif old_exists and new_exists:
                cursor.execute(f"UPDATE companies SET {current} = COALESCE({current}, {legacy})")
                cursor.execute(f"ALTER TABLE companies DROP COLUMN {legacy}")
        cursor.execute("""
            ALTER TABLE companies
            ADD COLUMN IF NOT EXISTS job_open_status VARCHAR(20) NOT NULL DEFAULT 'Open'
        """)
        cursor.execute("""
            ALTER TABLE companies
            ADD COLUMN IF NOT EXISTS application_status VARCHAR(30) NOT NULL DEFAULT 'None'
        """)
        # Keep unsaved legacy rows from appearing as if the user explicitly saved them.
        cursor.execute("""
            UPDATE companies
            SET application_status = 'None'
            WHERE is_kept = 0 AND is_rejected = 0 AND application_status = 'Saved'
        """)
        cursor.execute("""
            ALTER TABLE companies
            MODIFY COLUMN application_status VARCHAR(30) NOT NULL DEFAULT 'None'
        """)
        cursor.execute("""
            ALTER TABLE companies
            ADD COLUMN IF NOT EXISTS notes TEXT NULL
        """)
        cursor.execute("ALTER TABLE companies ADD COLUMN IF NOT EXISTS listing_skills TEXT NULL")
        cursor.execute("ALTER TABLE companies ADD COLUMN IF NOT EXISTS listing_details TEXT NULL")
        cursor.execute("""
            ALTER TABLE companies
            ADD COLUMN IF NOT EXISTS source_type VARCHAR(50) NOT NULL DEFAULT 'SearXNG'
        """)
        cursor.execute("""
            ALTER TABLE companies
            ADD COLUMN IF NOT EXISTS is_rejected TINYINT(1) NOT NULL DEFAULT 0
        """)
        cursor.execute("ALTER TABLE companies ADD COLUMN IF NOT EXISTS rejection_reason VARCHAR(40) NULL")
        cursor.execute("""
            ALTER TABLE companies
            ADD COLUMN IF NOT EXISTS rejected_at TIMESTAMP NULL DEFAULT NULL
        """)
        # What the listing looked like before rejection, so Restore can put it back.
        cursor.execute("ALTER TABLE companies ADD COLUMN IF NOT EXISTS pre_reject_kept TINYINT(1) NULL")
        cursor.execute("ALTER TABLE companies ADD COLUMN IF NOT EXISTS pre_reject_status VARCHAR(30) NULL")
        cursor.execute("""ALTER TABLE companies ADD COLUMN IF NOT EXISTS city VARCHAR(150) NULL""")
        cursor.execute("""ALTER TABLE companies ADD COLUMN IF NOT EXISTS latitude DECIMAL(10,7) NULL""")
        cursor.execute("""ALTER TABLE companies ADD COLUMN IF NOT EXISTS longitude DECIMAL(10,7) NULL""")
        cursor.execute("""ALTER TABLE companies ADD COLUMN IF NOT EXISTS distance_miles DECIMAL(8,2) NULL""")
        cursor.execute("""ALTER TABLE companies ADD COLUMN IF NOT EXISTS result_updated_at TIMESTAMP NULL DEFAULT NULL""")
        cursor.execute("""ALTER TABLE companies ADD COLUMN IF NOT EXISTS work_arrangement VARCHAR(20) NULL""")
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS search_history (
                id INT AUTO_INCREMENT PRIMARY KEY,
                job_title TEXT NOT NULL,
                state VARCHAR(100) NOT NULL,
                cities_json TEXT NULL,
                searched_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cursor.execute("""
            ALTER TABLE search_history
            MODIFY COLUMN job_title TEXT NOT NULL
        """)
        cursor.execute("""ALTER TABLE search_history ADD COLUMN IF NOT EXISTS cities_json TEXT NULL""")
        # One row per posting rather than per website, so an employer can have many openings.
        index_status = ensure_unique_source_index(cursor)
        if index_status:
            print(f"Database index update: {index_status}")
        connection.commit()
        _schema_ready.add("tracking")
    except Error as error:
        print()
        print("Could not ensure job tracking fields exist.")
        print(error)
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None and connection.is_connected():
            connection.close()


def tidy_expired_closed_jobs():
    """At startup: delete jobs closed for more than 30 days, keeping saved ones (see job_retention.py)."""
    connection = None
    try:
        connection = mysql.connector.connect(
            host=DB_HOST,
            port=DB_PORT,
            user=DB_USER,
            password=DB_PASSWORD,
            database=DB_NAME,
        )
        deleted = tidy_closed_jobs(connection)
        if deleted:
            print(f"Deleted {deleted} job(s) closed for more than 30 days (saved jobs are kept).")
    except Error as error:
        print()
        print("Could not tidy closed jobs.")
        print(error)
    finally:
        if connection is not None and connection.is_connected():
            connection.close()


def record_search_history(job_title, state, cities=None):
    ensure_job_tracking_columns()
    connection = None
    cursor = None
    try:
        connection = mysql.connector.connect(
            host=DB_HOST, port=DB_PORT, user=DB_USER, password=DB_PASSWORD, database=DB_NAME
        )
        cursor = connection.cursor()
        cursor.execute(
            "INSERT INTO search_history (job_title, state, cities_json) VALUES (%s, %s, %s)",
            (job_title, state, json.dumps(cities or [])),
        )
        connection.commit()
    except Error as error:
        print("Could not record search history.")
        print(error)
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None and connection.is_connected():
            connection.close()


def get_search_history(limit=10):
    ensure_job_tracking_columns()
    connection = None
    cursor = None
    try:
        connection = mysql.connector.connect(
            host=DB_HOST, port=DB_PORT, user=DB_USER, password=DB_PASSWORD, database=DB_NAME
        )
        cursor = connection.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT job_title, state, cities_json, searched_at
            FROM search_history
            ORDER BY searched_at DESC
            LIMIT %s
            """,
            (limit,),
        )
        return cursor.fetchall()
    except Error as error:
        print("Could not read search history.")
        print(error)
        return []
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None and connection.is_connected():
            connection.close()


def get_dashboard_counts():
    ensure_job_tracking_columns()
    connection = None
    cursor = None
    counts = {"saved": 0, "applied": 0, "recruiter": 0, "interview": 0, "closed": 0}
    try:
        connection = mysql.connector.connect(
            host=DB_HOST, port=DB_PORT, user=DB_USER, password=DB_PASSWORD, database=DB_NAME
        )
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


def ensure_profile_tables():
    if "profile" in _schema_ready:
        return
    connection = None
    cursor = None

    try:
        connection = mysql.connector.connect(
            host=DB_HOST,
            port=DB_PORT,
            user=DB_USER,
            password=DB_PASSWORD,
            database=DB_NAME,
        )
        cursor = connection.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS user_profile (
                id TINYINT PRIMARY KEY,
                first_name VARCHAR(100) NOT NULL DEFAULT '',
                last_name VARCHAR(100) NOT NULL DEFAULT '',
                state VARCHAR(100) NOT NULL DEFAULT '',
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS user_profile_job_titles (
                id INT AUTO_INCREMENT PRIMARY KEY,
                profile_id TINYINT NOT NULL,
                job_title VARCHAR(255) NOT NULL,
                UNIQUE KEY unique_profile_job_title (profile_id, job_title)
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS user_profile_cities (
                id INT AUTO_INCREMENT PRIMARY KEY,
                profile_id TINYINT NOT NULL,
                city VARCHAR(150) NOT NULL,
                radius_miles INT NOT NULL DEFAULT 50,
                UNIQUE KEY unique_profile_city (profile_id, city)
            )
        """)
        for column, definition in (
            ("home_location", "VARCHAR(150) NOT NULL DEFAULT ''"),
            ("home_zip", "VARCHAR(10) NOT NULL DEFAULT ''"),
            ("primary_job_title", "VARCHAR(255) NOT NULL DEFAULT ''"),
            ("avatar_data", "MEDIUMTEXT NULL"),
            ("work_preferences", "TEXT NULL"),
        ):
            cursor.execute(f"ALTER TABLE user_profile ADD COLUMN IF NOT EXISTS {column} {definition}")
        cursor.execute("""CREATE TABLE IF NOT EXISTS user_profile_skills (
            id INT AUTO_INCREMENT PRIMARY KEY, profile_id TINYINT NOT NULL,
            skill VARCHAR(80) NOT NULL, UNIQUE KEY unique_profile_skill (profile_id, skill))""")
        cursor.execute("""CREATE TABLE IF NOT EXISTS user_profile_work_history (
            id INT AUTO_INCREMENT PRIMARY KEY, profile_id TINYINT NOT NULL,
            company VARCHAR(150) NOT NULL DEFAULT '', role VARCHAR(150) NOT NULL DEFAULT '',
            dates VARCHAR(100) NOT NULL DEFAULT '', description TEXT NULL)""")
        cursor.execute("""
            INSERT IGNORE INTO user_profile (id, first_name, last_name, state)
            VALUES (1, '', '', '')
        """)
        connection.commit()
        _schema_ready.add("profile")
    except Error as error:
        print()
        print("Could not ensure profile tables exist.")
        print(error)
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None and connection.is_connected():
            connection.close()


def get_user_profile():
    ensure_profile_tables()
    connection = None
    cursor = None

    profile = {
        "first_name": "",
        "last_name": "",
        "state": "",
        "home_location": "", "home_zip": "", "primary_job_title": "", "avatar_data": "", "skills": [], "work_history": [], "work_preferences": [],
        "job_titles": [],
        "cities": [],
    }

    try:
        connection = mysql.connector.connect(
            host=DB_HOST,
            port=DB_PORT,
            user=DB_USER,
            password=DB_PASSWORD,
            database=DB_NAME,
        )
        cursor = connection.cursor(dictionary=True)
        cursor.execute("""
            SELECT first_name, last_name, state, home_location, home_zip, primary_job_title, avatar_data, work_preferences
            FROM user_profile
            WHERE id = 1
        """)
        row = cursor.fetchone()
        if row:
            profile.update(row)
            try:
                profile["work_preferences"] = json.loads(row.get("work_preferences") or "[]")
            except ValueError:
                profile["work_preferences"] = []

        cursor.execute("""
            SELECT job_title
            FROM user_profile_job_titles
            WHERE profile_id = 1
            ORDER BY job_title
        """)
        profile["job_titles"] = [row["job_title"] for row in cursor.fetchall()]
        cursor.execute("""
            SELECT city, radius_miles
            FROM user_profile_cities
            WHERE profile_id = 1
            ORDER BY city
        """)
        profile["cities"] = cursor.fetchall()
        cursor.execute("SELECT skill FROM user_profile_skills WHERE profile_id = 1 ORDER BY skill")
        profile["skills"] = [row["skill"] for row in cursor.fetchall()]
        cursor.execute("SELECT company, role, dates, description FROM user_profile_work_history WHERE profile_id = 1 ORDER BY id")
        profile["work_history"] = cursor.fetchall()
        return profile
    except Error as error:
        print()
        print("Could not read user profile from the database.")
        print(error)
        return profile
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None and connection.is_connected():
            connection.close()


def save_user_profile(first_name, last_name, state, job_titles, cities=None, *, home_location=None, home_zip=None,
                      primary_job_title=None, skills=None, work_history=None, avatar_data=None, work_preferences=None):
    ensure_profile_tables()
    connection = None
    cursor = None

    try:
        connection = mysql.connector.connect(
            host=DB_HOST,
            port=DB_PORT,
            user=DB_USER,
            password=DB_PASSWORD,
            database=DB_NAME,
        )
        cursor = connection.cursor()
        cursor.execute("""
            UPDATE user_profile
            SET first_name = %s, last_name = %s, state = %s
            WHERE id = 1
        """, (first_name, last_name, state))
        if home_location is not None:
            cursor.execute("UPDATE user_profile SET home_location = %s WHERE id = 1", (home_location[:150],))
        if home_zip is not None:
            cursor.execute("UPDATE user_profile SET home_zip = %s WHERE id = 1", (home_zip,))
        if primary_job_title is not None:
            cursor.execute("UPDATE user_profile SET primary_job_title = %s WHERE id = 1", (primary_job_title[:255],))
        if avatar_data is not None:
            cursor.execute("UPDATE user_profile SET avatar_data = %s WHERE id = 1", (avatar_data,))
        if work_preferences is not None:
            cursor.execute("UPDATE user_profile SET work_preferences = %s WHERE id = 1", (json.dumps(work_preferences),))
        cursor.execute("DELETE FROM user_profile_job_titles WHERE profile_id = 1")

        for job_title in job_titles:
            cursor.execute("""
                INSERT INTO user_profile_job_titles (profile_id, job_title)
                VALUES (1, %s)
            """, (job_title,))

        cursor.execute("DELETE FROM user_profile_cities WHERE profile_id = 1")
        allowed_radii = {5, 10, 15, 20, 30, 50}
        for item in (cities or []):
            city = str(item.get("city", "")).strip()[:150]
            try:
                radius = int(item.get("radius", item.get("radius_miles", 50)))
            except (TypeError, ValueError):
                radius = 50
            if city and radius in allowed_radii:
                cursor.execute("""
                    INSERT IGNORE INTO user_profile_cities (profile_id, city, radius_miles)
                    VALUES (1, %s, %s)
                    """, (city, radius))

        if skills is not None:
            cursor.execute("DELETE FROM user_profile_skills WHERE profile_id = 1")
            for skill in normalize_skills(skills):
                cursor.execute("INSERT INTO user_profile_skills (profile_id, skill) VALUES (1, %s)", (skill,))
        if work_history is not None:
            cursor.execute("DELETE FROM user_profile_work_history WHERE profile_id = 1")
            for item in work_history[:50]:
                if not isinstance(item, dict):
                    continue
                company = str(item.get("company", "")).strip()[:150]
                role = str(item.get("role", "")).strip()[:150]
                dates = str(item.get("dates", "")).strip()[:100]
                description = str(item.get("description", "")).strip()[:3000]
                if company or role:
                    cursor.execute("""INSERT INTO user_profile_work_history
                        (profile_id, company, role, dates, description) VALUES (1, %s, %s, %s, %s)""",
                        (company, role, dates, description))

        connection.commit()
        return True
    except Error as error:
        print()
        print("Could not save user profile.")
        print(error)
        return False
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None and connection.is_connected():
            connection.close()


def read_block_metadata():
    try:
        data = json.loads(BLOCK_METADATA_FILE.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return {"domains": data.get("domains", {}), "companies": data.get("companies", {})}
    except (OSError, ValueError):
        pass
    return {"domains": {}, "companies": {}}


def save_block_metadata(kind, key, source=None):
    data = read_block_metadata()
    if source is None:
        data[kind].pop(key, None)
    elif key not in data[kind]:
        data[kind][key] = {"source": source, "blocked_at": datetime.now().astimezone().isoformat(timespec="seconds")}
    temp = BLOCK_METADATA_FILE.with_suffix(".tmp")
    temp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    temp.replace(BLOCK_METADATA_FILE)


def block_details(kind, names):
    data = read_block_metadata()[kind]
    recommended = set()
    if kind == "domains" and RECOMMENDED_DOMAINS_FILE.exists():
        recommended = {line.strip().lower() for line in RECOMMENDED_DOMAINS_FILE.read_text(encoding="utf-8").splitlines() if line.strip() and not line.startswith("#")}
    return {name: {"source": data.get(name.casefold(), {}).get("source") or ("Recommended" if name.lower() in recommended else "User"),
                   "blocked_at": data.get(name.casefold(), {}).get("blocked_at")}
            for name in names}


def add_domain_to_blocklist(domain):
    domain = (domain or "").strip().lower().removeprefix("www.")
    if not domain:
        return

    existing = set()
    if BLOCKED_DOMAINS_FILE.exists():
        existing = {
            line.strip().lower()
            for line in BLOCKED_DOMAINS_FILE.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.strip().startswith("#")
        }

    if domain in existing:
        return

    with BLOCKED_DOMAINS_FILE.open("a", encoding="utf-8") as file:
        if BLOCKED_DOMAINS_FILE.stat().st_size:
            file.write("\n")
        file.write(domain + "\n")
    save_block_metadata("domains", domain, "User")


def get_blocked_companies():
    if not BLOCKED_COMPANIES_FILE.exists():
        return []
    return sorted({line.strip() for line in BLOCKED_COMPANIES_FILE.read_text(encoding="utf-8").splitlines()
                   if line.strip() and not line.lstrip().startswith("#")}, key=str.casefold)


def add_blocked_company(name):
    name = " ".join((name or "").split())[:150]
    if not name or "\n" in name or "\r" in name:
        return False
    if name.casefold() not in {item.casefold() for item in get_blocked_companies()}:
        with BLOCKED_COMPANIES_FILE.open("a", encoding="utf-8") as file:
            file.write(name + "\n")
        save_block_metadata("companies", name.casefold(), "User")
    return True


@app.route("/settings/blocked-companies", methods=["POST"])
def manage_blocked_company():
    name = " ".join(request.form.get("company", "").split())[:150]
    action = request.form.get("action", "")
    if not name:
        return redirect("/rejected-listings?company_notice=invalid#blocked-companies")
    if action == "add":
        add_blocked_company(name)
    elif action == "remove":
        names = [item for item in get_blocked_companies() if item.casefold() != name.casefold()]
        BLOCKED_COMPANIES_FILE.write_text("\n".join(names) + ("\n" if names else ""), encoding="utf-8")
        save_block_metadata("companies", name.casefold())
    else:
        abort(400)
    return redirect("/rejected-listings?company_notice=" + action + "#blocked-companies")


@app.route("/block-company/<int:company_id>", methods=["POST"])
def block_company(company_id):
    ensure_job_tracking_columns()
    connection = None
    cursor = None
    try:
        connection = mysql.connector.connect(host=DB_HOST, port=DB_PORT, user=DB_USER, password=DB_PASSWORD, database=DB_NAME)
        cursor = connection.cursor(dictionary=True)
        cursor.execute("SELECT name FROM companies WHERE id = %s", (company_id,))
        row = cursor.fetchone()
        if not row or not add_blocked_company(row.get("name")):
            return api_error("E3220", "No company name was available to block.", 404)
        return jsonify({"status": "blocked", "company": row["name"]})
    except Error as error:
        log_error_code("E3221", f"Could not block company: {error}")
        return api_error("E3221", "Could not block this company.", 500)
    finally:
        if cursor is not None: cursor.close()
        if connection is not None and connection.is_connected(): connection.close()


def get_blocked_domains():
    if not BLOCKED_DOMAINS_FILE.exists():
        return []
    return sorted({line.strip().lower() for line in BLOCKED_DOMAINS_FILE.read_text(encoding="utf-8").splitlines()
                   if line.strip() and not line.lstrip().startswith("#")})


@app.route("/settings/blocked-domains", methods=["POST"])
def manage_blocked_domain():
    domain = request.form.get("domain", "").strip().lower().removeprefix("www.")
    action = request.form.get("action", "")
    if not re.fullmatch(r"(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}", domain):
        return redirect("/rejected-listings?domain_notice=invalid#blocked-domains")
    if action == "add":
        add_domain_to_blocklist(domain)
    elif action == "remove":
        remove_domain_from_blocklist(domain)
    else:
        abort(400)
    return redirect("/rejected-listings?domain_notice=" + action + "#blocked-domains")


def remove_domain_from_blocklist(domain):
    domain = (domain or "").strip().lower().removeprefix("www.")
    if not domain or not BLOCKED_DOMAINS_FILE.exists():
        return

    lines = BLOCKED_DOMAINS_FILE.read_text(encoding="utf-8").splitlines()
    kept = []
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and stripped.lower() == domain:
            continue
        kept.append(line)

    BLOCKED_DOMAINS_FILE.write_text("\n".join(kept).rstrip() + "\n", encoding="utf-8")
    save_block_metadata("domains", domain)


def get_rejected_companies():
    ensure_job_tracking_columns()
    connection = None
    cursor = None
    try:
        connection = mysql.connector.connect(
            host=DB_HOST, port=DB_PORT, user=DB_USER, password=DB_PASSWORD, database=DB_NAME
        )
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
    ensure_job_tracking_columns()
    connection = None
    cursor = None

    try:
        connection = mysql.connector.connect(
            host=DB_HOST, port=DB_PORT, user=DB_USER, password=DB_PASSWORD, database=DB_NAME
        )
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
        connection = mysql.connector.connect(
            host=DB_HOST,
            port=DB_PORT,
            user=DB_USER,
            password=DB_PASSWORD,
            database=DB_NAME,
        )

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
        blocked_domains = set(get_blocked_domains())
        blocked_names = {name.casefold() for name in get_blocked_companies()}
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
                f"Details were saved to {SCRAPER_LOG_FILE.name}."
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
        current_version = tuple(int(part) for part in APP_VERSION.split("."))
        latest_version, latest_path = find_latest_update_zip()

        if latest_version is None:
            return jsonify({
                "status": "none_found",
                "current_version": APP_VERSION,
                "message": "No Job Finder update ZIPs were found in Downloads or the Python folder.",
            })

        latest_text = ".".join(str(part) for part in latest_version)
        return jsonify({
            "status": "update_available" if latest_version > current_version else "current",
            "current_version": APP_VERSION,
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
    current_version = tuple(int(part) for part in APP_VERSION.split("."))
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
    return jsonify({"version": APP_VERSION})


EXTENSION_ID = "web-job-scraper@jamie.local"
EXTENSION_FILE = re.compile(r"^web-job-scraper-v(\d+)\.(\d+)\.(\d+)\.xpi$")


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
    profile = get_user_profile()
    titles = [spelling_fix(title) for title in
              [profile.get("primary_job_title") or ""] + list(profile.get("job_titles") or [])]  # typos fixed, as in searches
    return jsonify({
        "titles": [title for i, title in enumerate(titles) if title and title.casefold() not in
                   {t.casefold() for t in titles[:i]}],
        "skills": profile.get("skills") or [],
        "work_preferences": profile.get("work_preferences") or [],
        "blocked_companies": get_blocked_companies(),  # companies you blocked in Job Finder: hidden on LinkedIn too
        "skill_aliases": SKILL_ALIASES,
        "ambiguous_skills": sorted(AMBIGUOUS_SKILLS),
    })


@app.route("/extension/distances")
def extension_distances():
    """Estimated distance and 6 a.m. drive time from the home ZIP to each place (places separated by |), for the
    LinkedIn markers. Same estimate as the Dashboard (travel.py); nothing is looked up online. No CORS header,
    so only the extension can read it."""
    profile = get_user_profile()
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
    ensure_job_tracking_columns()
    companies = get_companies()

    running, mode = scraper_status()
    profile = get_user_profile()
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
    ensure_job_tracking_columns()
    status_filter = request.args.get("status", "").strip()[:30]
    state_filter = request.args.get("state", "").strip()[:100]
    title_filter = request.args.get("title", "").strip()[:255]
    sort_by = request.args.get("sort", "date_desc").strip()[:30]
    companies = get_kept_companies(status_filter, state_filter, title_filter, sort_by)
    profile = get_user_profile()
    add_job_fit(companies, profile.get("skills", []))
    add_drive_times(companies, profile.get("home_zip"), profile.get("state") or "")
    return render_template(
        "user-dashboard.html",
        companies=companies,
        profile=profile,
        counts=get_dashboard_counts(),
        search_history=get_search_history(),
        skill_suggestions=profile_skill_suggestions(profile),
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
        return json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
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
    temporary = Path(str(SETTINGS_FILE) + ".tmp")
    try:
        temporary.write_text(json.dumps(updated, indent="\t") + "\n", encoding="utf-8")
        os.replace(temporary, SETTINGS_FILE)
    except OSError as error:
        log_error_code("E4101", f"Could not save settings.json: {error}")
        return redirect("/tuning?error=" + quote("Could not save the settings file.") + "#search-settings")
    return redirect("/tuning?saved=1#search-settings")


@app.route("/credibility-scores")
def credibility_scores():
    return render_template("credibility-scores.html")


@app.route("/rejected-listings")
def rejected_listings():
    ensure_job_tracking_columns()
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
    return render_template(
        "rejected-listings.html",
        companies=get_rejected_companies(),
        search_skips=search_skips,
        blocked_domains=get_blocked_domains(),
        blocked_companies=get_blocked_companies(),
        blocked_domain_info=block_details("domains", get_blocked_domains()),
        blocked_company_info=block_details("companies", get_blocked_companies()),
        company_notice=request.args.get("company_notice", ""),
        domain_notice=request.args.get("domain_notice", ""),
    )


@app.route("/reject-listing/<int:company_id>", methods=["POST"])
def reject_listing(company_id):
    ensure_job_tracking_columns()
    wants_json = request.headers.get("X-Requested-With") == "fetch" or request.accept_mimetypes.best == "application/json"
    connection = None
    cursor = None
    domain = None
    reason = (request.get_json(silent=True) or {}).get("reason") if request.is_json else request.form.get("reason")
    if reason not in {"wrong_role", "wrong_location", "not_a_job", "duplicate", "other"}:
        reason = "other"
    try:
        connection = mysql.connector.connect(
            host=DB_HOST, port=DB_PORT, user=DB_USER, password=DB_PASSWORD, database=DB_NAME
        )
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
                rejected_at = CURRENT_TIMESTAMP, rejection_reason = %s
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
    ensure_job_tracking_columns()
    wants_json = request.headers.get("X-Requested-With") == "fetch" or request.accept_mimetypes.best == "application/json"
    connection = None
    cursor = None
    domain = None
    try:
        connection = mysql.connector.connect(
            host=DB_HOST, port=DB_PORT, user=DB_USER, password=DB_PASSWORD, database=DB_NAME
        )
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
                rejected_at = NULL, rejection_reason = NULL,
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
    ensure_job_tracking_columns()
    connection = None
    cursor = None
    try:
        connection = mysql.connector.connect(host=DB_HOST, port=DB_PORT, user=DB_USER, password=DB_PASSWORD, database=DB_NAME)
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
        title = part.strip()[:255]
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
    primary = request.form.get("primary_job_title", "").strip()[:255]
    if primary:
        job_titles = [primary] + [title for title in job_titles if title.casefold() != primary.casefold()]
    avatar = request.form.get("avatar_data", "")
    if avatar and (not re.fullmatch(r"data:image/jpeg;base64,[A-Za-z0-9+/=]+", avatar) or len(avatar) > 550000):
        abort(400)
    save_user_profile(first_name, last_name, state, job_titles, cities,
                      home_location=home_location, home_zip=home_zip, primary_job_title=primary,
                      skills=read_list("skills_json"), work_history=read_list("work_history_json"),
                      avatar_data=avatar if "avatar_data" in request.form else None,
                      work_preferences=[value for value in request.form.getlist("work_preferences")
                                        if value in {"Part-time", "Full-time", "Contract", "Freelance / Gig", "Remote", "Hybrid", "Onsite"}])
    return redirect("/dashboard")


@app.route("/profile/save-title", methods=["POST"])
def save_profile_title():
    data = request.get_json(silent=True) or {}
    title = str(data.get("title", "")).strip()[:255]
    if not title:
        return api_error("E3301", "Enter a job title.")
    profile = get_user_profile()
    if title.casefold() in {s.casefold() for s in profile["job_titles"]}:
        return jsonify({"status": "already_saved"})
    profile["job_titles"].append(title)
    if not save_user_profile(profile["first_name"], profile["last_name"], profile["state"],
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
        connection = mysql.connector.connect(
            host=DB_HOST,
            port=DB_PORT,
            user=DB_USER,
            password=DB_PASSWORD,
            database=DB_NAME,
        )
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
    ensure_job_tracking_columns()
    connection = None
    cursor = None
    try:
        connection = mysql.connector.connect(
            host=DB_HOST, port=DB_PORT, user=DB_USER, password=DB_PASSWORD, database=DB_NAME
        )
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
        STOP_REQUEST_FILE.unlink(missing_ok=True)


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
                    STOP_REQUEST_FILE.touch()
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
    ensure_job_tracking_columns()
    application_status = request.form.get("application_status", "None").strip()
    notes = request.form.get("notes", "").strip()[:5000]
    if application_status not in APPLICATION_STATUSES:
        application_status = "None"

    connection = None
    cursor = None
    try:
        connection = mysql.connector.connect(
            host=DB_HOST, port=DB_PORT, user=DB_USER, password=DB_PASSWORD, database=DB_NAME
        )
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
        connection = mysql.connector.connect(
            host=DB_HOST,
            port=DB_PORT,
            user=DB_USER,
            password=DB_PASSWORD,
            database=DB_NAME,
        )
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
                profile = get_user_profile()
                titles = []
                for part in job_title.split(","):
                    title = part.strip()[:255]
                    if title and title.lower() not in {item.lower() for item in titles}:
                        titles.append(title)
                save_user_profile(
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
            STOP_REQUEST_FILE.unlink(missing_ok=True)
            with SCRAPER_LOG_FILE.open("a", encoding="utf-8") as log_file:
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
        _home_state_cache.update(value=(get_user_profile().get("state") or "")[:2], at=time.monotonic())
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
            with SCRAPER_LOG_FILE.open("a", encoding="utf-8") as log_file:
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
            with SCRAPER_LOG_FILE.open("a", encoding="utf-8") as log_file:
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
            with SCRAPER_LOG_FILE.open("a", encoding="utf-8") as log_file:
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
    if not SCRAPER_LOG_FILE.exists():
        return "Preparing the import"
    try:
        with SCRAPER_LOG_FILE.open("rb") as log_file:
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
    if not SCRAPER_LOG_FILE.exists():
        return "Preparing existing results"
    try:
        with SCRAPER_LOG_FILE.open("rb") as log_file:
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
    if not SCRAPER_LOG_FILE.exists():
        return {"progress": fallback, "passed": 0, "checked": 0, "saved": 0, "limit": None}
    try:
        with SCRAPER_LOG_FILE.open("rb") as log_file:
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
    if not running and SCRAPER_LOG_FILE.exists():
        try:
            with SCRAPER_LOG_FILE.open("rb") as log_file:
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
    ensure_job_tracking_columns()
    ensure_profile_tables()
    tidy_expired_closed_jobs()
    app.run(host="127.0.0.1", port=5000, debug=False, use_reloader=False)
