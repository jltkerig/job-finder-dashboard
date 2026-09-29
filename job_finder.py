import argparse
import json
import math
import time
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

import mysql.connector
import requests
from bs4 import BeautifulSoup
from mysql.connector import Error
from dotenv import load_dotenv
from profile_tools import listing_skills
from remote_ok import fetch_jobs as fetch_remote_ok_jobs, matching_jobs as matching_remote_ok_jobs

# =========================================================
# FILES
# =========================================================

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

SETTINGS_FILE = BASE_DIR / "settings.json"
BLOCKED_DOMAINS_FILE = BASE_DIR / "blocked_domains.txt"
BLOCKED_COMPANIES_FILE = BASE_DIR / "blocked_companies.txt"
BLOCKED_COUNTRY_DOMAINS_FILE = BASE_DIR / "blocked_country_domains.txt"

SEARXNG_COMPOSE_FILE = BASE_DIR / "searxng" / "docker-compose.yml"


# =========================================================
# LOAD SETTINGS
# =========================================================


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


# =========================================================
# PROGRAM SETTINGS
# =========================================================

SEARXNG_URL = "http://localhost:8080/search"
SEARXNG_CONTAINER = "searxng"

SEARXNG_MAX_RUNTIME = settings["searxng_timeout_minutes"] * 60

MAX_SEARCH_RESULTS = settings["max_search_results"]
MAX_SEARCH_PAGES = settings.get("max_search_pages", 20)
REQUEST_DELAY = settings["request_delay_seconds"]
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

USER_AGENT = "PersonalJobFinder/1.0"
MAX_HTML_SIZE = 2_000_000

HEADERS = {"User-Agent": USER_AGENT}

CAREER_CREDIBILITY_THRESHOLD = 6
USA_CREDIBILITY_THRESHOLD = 5
MAX_DISCOVERY_PAGES = 8


# =========================================================
# DATABASE SETTINGS
# =========================================================

DB_HOST = os.getenv("DB_HOST", "127.0.0.1")
DB_PORT = int(os.getenv("DB_PORT", "3306"))
DB_USER = os.getenv("DB_USER", "root")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")
DB_NAME = os.getenv("DB_NAME", "job_finder")


# =========================================================
# US LOCATION DATA
# =========================================================

US_STATES = {
    "alabama": "AL",
    "alaska": "AK",
    "arizona": "AZ",
    "arkansas": "AR",
    "california": "CA",
    "colorado": "CO",
    "connecticut": "CT",
    "delaware": "DE",
    "florida": "FL",
    "georgia": "GA",
    "hawaii": "HI",
    "idaho": "ID",
    "illinois": "IL",
    "indiana": "IN",
    "iowa": "IA",
    "kansas": "KS",
    "kentucky": "KY",
    "louisiana": "LA",
    "maine": "ME",
    "maryland": "MD",
    "massachusetts": "MA",
    "michigan": "MI",
    "minnesota": "MN",
    "mississippi": "MS",
    "missouri": "MO",
    "montana": "MT",
    "nebraska": "NE",
    "nevada": "NV",
    "new hampshire": "NH",
    "new jersey": "NJ",
    "new mexico": "NM",
    "new york": "NY",
    "north carolina": "NC",
    "north dakota": "ND",
    "ohio": "OH",
    "oklahoma": "OK",
    "oregon": "OR",
    "pennsylvania": "PA",
    "rhode island": "RI",
    "south carolina": "SC",
    "south dakota": "SD",
    "tennessee": "TN",
    "texas": "TX",
    "utah": "UT",
    "vermont": "VT",
    "virginia": "VA",
    "washington": "WA",
    "west virginia": "WV",
    "wisconsin": "WI",
    "wyoming": "WY",
}

US_STATE_ABBREVIATIONS = set(US_STATES.values())


# =========================================================
# GLOBAL STATE
# =========================================================

searxng_start_time = None
docker_started_by_program = False
update_existing_mode = False


# =========================================================
# COMMAND HELPERS
# =========================================================


def run_command(command):
    try:
        return subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
        )

    except FileNotFoundError:
        return None


def run_docker_command(arguments):
    return run_command(["docker"] + arguments)


# =========================================================
# DOCKER DESKTOP
# =========================================================


def docker_engine_running():
    result = run_docker_command(["info"])

    if result is None:
        return False

    return result.returncode == 0


def start_docker_desktop():
    global docker_started_by_program

    if docker_engine_running():
        print("Docker is already running.")
        return True

    if not START_DOCKER_AUTOMATICALLY:
        print()
        print("Docker is not running.")
        print("Start Docker Desktop and " "run Job Finder again.")
        return False

    print("Starting Docker Desktop...")

    result = run_docker_command(
        [
            "desktop",
            "start",
        ]
    )

    if result is None:
        print("Docker command was not found.")
        return False

    if result.returncode != 0:
        print("Docker Desktop could not " "be started automatically.")

        print(result.stderr.strip())

        return False

    docker_started_by_program = True

    print("Waiting for Docker...")

    for _ in range(60):
        if docker_engine_running():
            print("Docker is running.")
            return True

        time.sleep(2)

    print("Docker did not become ready.")

    return False


def stop_docker_desktop():
    if not STOP_DOCKER_WHEN_FINISHED:
        return

    if not docker_started_by_program:
        print("Docker was already running " "before Job Finder started.")
        print("Leaving Docker running.")
        return

    print()
    print("Stopping Docker Desktop...")

    result = run_docker_command(
        [
            "desktop",
            "stop",
        ]
    )

    if result is not None and result.returncode == 0:
        print("Docker Desktop stopped.")

    else:
        print("Docker Desktop could not " "be stopped automatically.")


# =========================================================
# SEARXNG
# =========================================================


def searxng_is_running():
    result = run_docker_command(
        [
            "ps",
            "--filter",
            f"name={SEARXNG_CONTAINER}",
            "--format",
            "{{.Names}}",
        ]
    )

    if result is None:
        return False

    containers = result.stdout.strip().splitlines()

    return SEARXNG_CONTAINER in containers


def searxng_exists():
    result = run_docker_command(
        [
            "ps",
            "-a",
            "--filter",
            f"name={SEARXNG_CONTAINER}",
            "--format",
            "{{.Names}}",
        ]
    )

    if result is None:
        return False

    containers = result.stdout.strip().splitlines()

    return SEARXNG_CONTAINER in containers


def wait_for_searxng():
    print("Waiting for SearXNG...")

    for _ in range(30):
        try:
            response = requests.get(
                "http://localhost:8080",
                timeout=2,
            )

            if response.ok:
                return True

        except requests.RequestException:
            pass

        time.sleep(1)

    return False


def start_searxng():
    global searxng_start_time

    if searxng_is_running():
        print("SearXNG is already running.")

        searxng_start_time = time.time()

        return True

    print("Starting SearXNG...")

    result = run_docker_command(
        [
            "compose",
            "--env-file",
            str(BASE_DIR / ".env"),
            "-f",
            str(SEARXNG_COMPOSE_FILE),
            "up",
            "-d",
        ]
    )

    if result is None:
        return False

    if result.returncode != 0:
        print("Could not start SearXNG.")
        print(result.stderr.strip())

        return False

    if not wait_for_searxng():
        print("SearXNG did not become ready.")

        return False

    searxng_start_time = time.time()

    print("SearXNG is running.")

    print("Maximum runtime: " f"{settings['searxng_timeout_minutes']} " "minutes.")

    return True


def stop_searxng():
    if not searxng_is_running():
        return

    print()
    print("Stopping SearXNG...")

    result = run_docker_command(
        [
            "stop",
            SEARXNG_CONTAINER,
        ]
    )

    if result is not None and result.returncode == 0:
        print("SearXNG stopped.")


def check_searxng_timer():
    if not searxng_is_running():
        print()
        print("SearXNG has stopped.")

        return False

    if searxng_start_time is None:
        return True

    elapsed = time.time() - searxng_start_time

    if elapsed >= SEARXNG_MAX_RUNTIME:
        print()
        print("SearXNG reached its " "time limit.")

        stop_searxng()

        return False

    return True


# =========================================================
# DATABASE
# =========================================================


def connect_database():
    try:
        return mysql.connector.connect(
            host=DB_HOST,
            port=DB_PORT,
            user=DB_USER,
            password=DB_PASSWORD,
            database=DB_NAME,
        )

    except Error as error:
        print()
        print("Could not connect " "to the database.")

        print(error)

        return None


def ensure_database_schema(connection):
    cursor = None

    try:
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

        required_columns = {
            "career_job_title": "VARCHAR(255) NULL AFTER name",
            "career_credibility": "INT NULL AFTER career_job_title",
            "job_open_status": "VARCHAR(20) NOT NULL DEFAULT 'Open' AFTER career_credibility",
            "application_status": "VARCHAR(30) NOT NULL DEFAULT 'None' AFTER job_open_status",
            "notes": "TEXT NULL AFTER application_status",
            "source_type": "VARCHAR(50) NOT NULL DEFAULT 'SearXNG' AFTER source_url",
            "city": "VARCHAR(150) NULL AFTER state",
            "latitude": "DECIMAL(10,7) NULL AFTER city",
            "longitude": "DECIMAL(10,7) NULL AFTER latitude",
            "distance_miles": "DECIMAL(8,2) NULL AFTER longitude",
            "result_updated_at": "TIMESTAMP NULL DEFAULT NULL AFTER last_checked",
            "work_arrangement": "VARCHAR(20) NULL AFTER distance_miles",
            "listing_skills": "TEXT NULL AFTER work_arrangement",
            "is_rejected": "TINYINT(1) NOT NULL DEFAULT 0 AFTER is_kept",
        }

        for column_name, definition in required_columns.items():
            cursor.execute(
                "SHOW COLUMNS FROM companies LIKE %s",
                (column_name,),
            )

            if cursor.fetchone() is None:
                cursor.execute(
                    f"ALTER TABLE companies ADD COLUMN {column_name} {definition}"
                )

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS geocode_cache (
                query_text VARCHAR(255) PRIMARY KEY,
                latitude DECIMAL(10,7) NOT NULL,
                longitude DECIMAL(10,7) NOT NULL,
                display_name TEXT NULL,
                updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
            )
        """)
        connection.commit()
        return True

    except Error as error:
        print()
        print("Could not update database schema.")
        print(error)
        return False

    finally:
        if cursor is not None:
            cursor.close()


def clear_companies_table(connection):
    cursor = None

    try:
        cursor = connection.cursor()

        cursor.execute("TRUNCATE TABLE companies")

        connection.commit()

        print()
        print("Previous test company data cleared.")

        return True

    except Error as error:
        print()
        print("Could not clear companies table.")
        print(error)

        return False

    finally:
        if cursor is not None:
            cursor.close()


def save_company(
    connection,
    name,
    career_job_title,
    career_credibility,
    domain,
    career_url,
    source_url,
    country,
    state,
    usa_credibility,
    city=None,
    latitude=None,
    longitude=None,
    distance_miles=None,
    work_arrangement=None,
    skills=None,
    source_type="SearXNG",
):
    now = datetime.now(timezone.utc)
    cursor = None

    try:
        cursor = connection.cursor()

        # Feed items have stable source URLs; a provider domain is shared by many employers.
        if source_type == "Remote OK":
            cursor.execute("SELECT id FROM companies WHERE source_type = %s AND source_url = %s LIMIT 1", (source_type, source_url))
        else:
            cursor.execute(
            """
            SELECT id
            FROM companies
            WHERE LOWER(TRIM(domain)) = LOWER(TRIM(%s))
              AND LOWER(TRIM(COALESCE(career_job_title, ''))) = LOWER(TRIM(%s))
            LIMIT 1
            """,
            (domain, career_job_title or ""),
            )
        duplicate = cursor.fetchone()
        if duplicate:
            cursor.execute(
                """
                UPDATE companies
                SET name = %s, career_credibility = %s, career_url = %s, source_url = %s,
                    source_type = %s, country = %s, state = %s, city = %s, latitude = %s, longitude = %s, distance_miles = %s, usa_credibility = %s, work_arrangement = %s,
                    listing_skills = %s, job_open_status = 'Open', last_checked = %s
                WHERE id = %s
                """,
                (name, career_credibility, career_url, source_url, source_type, country, state, city, latitude, longitude, distance_miles, usa_credibility, work_arrangement, json.dumps(skills or []), now, duplicate[0]),
            )
            connection.commit()
            return False

        sql = """
        INSERT INTO companies
        (
            name,
            career_job_title,
            career_credibility,
            domain,
            career_url,
            source_url,
            source_type,
            job_open_status,
            country,
            state,
            city,
            latitude,
            longitude,
            distance_miles,
            usa_credibility,
            work_arrangement,
            listing_skills,
            date_found,
            last_checked
        )
        VALUES
        (
            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
        )
        ON DUPLICATE KEY UPDATE
            name = VALUES(name),
            career_job_title = VALUES(career_job_title),
            career_credibility = VALUES(career_credibility),
            career_url = VALUES(career_url),
            source_url = VALUES(source_url),
            source_type = VALUES(source_type),
            job_open_status = VALUES(job_open_status),
            country = VALUES(country),
            state = VALUES(state),
            city = VALUES(city),
            latitude = VALUES(latitude),
            longitude = VALUES(longitude),
            distance_miles = VALUES(distance_miles),
            usa_credibility = VALUES(usa_credibility),
            work_arrangement = VALUES(work_arrangement),
            listing_skills = VALUES(listing_skills),
            last_checked = VALUES(last_checked)
        """

        cursor.execute(
            sql,
            (
                name,
                career_job_title,
                career_credibility,
                domain,
                career_url,
                source_url,
                source_type,
                "Open",
                country,
                state,
                city,
                latitude,
                longitude,
                distance_miles,
                usa_credibility,
                work_arrangement,
                json.dumps(skills or []),
                now,
                now,
            ),
        )

        connection.commit()
        return cursor.rowcount == 1

    except Error as error:
        print(f"Could not save {domain}.")
        print(error)
        return False

    finally:
        if cursor is not None:
            cursor.close()


# =========================================================
# DOMAIN FILTERS
# =========================================================


def is_valid_url(url):
    parsed = urlparse(url)

    return parsed.scheme in {
        "http",
        "https",
    }


def get_domain(url):
    domain = urlparse(url).netloc.lower()

    return domain.removeprefix("www.")


def is_blocked_domain(domain):
    return any(
        domain == blocked or domain.endswith("." + blocked)
        for blocked in BLOCKED_DOMAINS
    )


def has_blocked_country_domain(domain):
    if not USA_ONLY:
        return False

    return any(domain.endswith(blocked) for blocked in BLOCKED_COUNTRY_DOMAINS)


# =========================================================
# SAFE REQUEST
# =========================================================


def safe_request(url):
    if not update_existing_mode and not check_searxng_timer():
        return None

    if not is_valid_url(url):
        return None

    try:
        time.sleep(REQUEST_DELAY)

        response = requests.get(
            url,
            headers=HEADERS,
            timeout=TIMEOUT,
            allow_redirects=True,
            stream=True,
        )

        response.raise_for_status()

        content_type = response.headers.get(
            "Content-Type",
            "",
        ).lower()

        if "text/html" not in content_type:
            response.close()
            return None

        content_length = response.headers.get("Content-Length")

        if content_length:
            try:
                if int(content_length) > MAX_HTML_SIZE:
                    response.close()
                    return None

            except ValueError:
                pass

        content = b""

        for chunk in response.iter_content(chunk_size=8192):
            if not chunk:
                continue

            content += chunk

            if len(content) > MAX_HTML_SIZE:
                response.close()
                return None

        response._content = content

        return response

    except requests.RequestException:
        return None


# =========================================================
# COMPANY NAME DETECTION
# =========================================================


def clean_company_name(
    name,
    domain,
):
    if not name:
        return None

    cleaned = name.strip()

    separators = [
        " | ",
        " - ",
        " – ",
        " — ",
        " :: ",
    ]

    for separator in separators:
        if separator in cleaned:
            cleaned = cleaned.split(separator)[0].strip()

    generic_names = {
        "home",
        "homepage",
        "welcome",
        "careers",
        "jobs",
        "official website",
    }

    if cleaned.lower() in generic_names:
        return None

    if len(cleaned) > 120:
        return None

    return cleaned or domain


def find_json_ld_company_name(data):
    if isinstance(data, list):
        for item in data:
            name = find_json_ld_company_name(item)

            if name:
                return name

        return None

    if not isinstance(
        data,
        dict,
    ):
        return None

    object_type = data.get("@type")

    valid_types = {
        "Organization",
        "Corporation",
        "LocalBusiness",
        "ProfessionalService",
        "WebSite",
    }

    if isinstance(
        object_type,
        list,
    ):
        type_matches = any(item in valid_types for item in object_type)

    else:
        type_matches = object_type in valid_types

    if type_matches:
        name = data.get("name")

        if name:
            return str(name).strip()

    for value in data.values():
        if isinstance(
            value,
            (dict, list),
        ):
            name = find_json_ld_company_name(value)

            if name:
                return name

    return None


def extract_company_name(
    soup,
    search_title,
    domain,
):
    og_site_name = soup.find(
        "meta",
        attrs={"property": "og:site_name"},
    )

    if og_site_name and og_site_name.get("content"):
        name = clean_company_name(
            og_site_name["content"],
            domain,
        )

        if name:
            return name

    scripts = soup.find_all(
        "script",
        type="application/ld+json",
    )

    for script in scripts:
        if not script.string:
            continue

        try:
            data = json.loads(script.string)

        except json.JSONDecodeError:
            continue

        json_name = find_json_ld_company_name(data)

        name = clean_company_name(
            json_name,
            domain,
        )

        if name:
            return name

    if soup.title and soup.title.string:
        name = clean_company_name(
            soup.title.string,
            domain,
        )

        if name:
            return name

    name = clean_company_name(
        search_title,
        domain,
    )

    if name:
        return name

    return domain


# =========================================================
# USA DETECTION
# =========================================================


def find_state_from_text(text):
    text_lower = text.lower()

    for state_name, abbreviation in US_STATES.items():
        if re.search(rf"\b{re.escape(state_name)}\b", text_lower):
            return abbreviation

    # Strong abbreviation contexts such as "Gaithersburg, MD" or "MD 20877".
    abbreviations = "|".join(sorted(US_STATE_ABBREVIATIONS))
    abbreviation_pattern = (
        rf",\s*({abbreviations})\b"
        rf"|\b({abbreviations})\s+\d{{5}}(?:-\d{{4}})?\b"
    )

    match = re.search(abbreviation_pattern, text)
    if match:
        return match.group(1) or match.group(2)

    return None


def iter_json_ld_objects(data):
    if isinstance(data, list):
        for item in data:
            yield from iter_json_ld_objects(item)
        return

    if not isinstance(data, dict):
        return

    yield data

    for value in data.values():
        if isinstance(value, (dict, list)):
            yield from iter_json_ld_objects(value)


def inspect_json_ld(soup):
    score = 0
    state = None
    evidence = []

    for script in soup.find_all("script", type="application/ld+json"):
        if not script.string:
            continue

        try:
            data = json.loads(script.string)
        except json.JSONDecodeError:
            continue

        for obj in iter_json_ld_objects(data):
            address = obj.get("address")
            if isinstance(address, dict):
                country = str(address.get("addressCountry", "")).strip().lower()
                region = str(address.get("addressRegion", "")).strip().upper()

                if country in {"us", "usa", "united states", "united states of america"}:
                    score += 5
                    evidence.append("structured addressCountry=US")

                if region in US_STATE_ABBREVIATIONS:
                    state = region
                    score += 3
                    evidence.append(f"structured addressRegion={region}")

            obj_type = obj.get("@type")
            types = set(obj_type if isinstance(obj_type, list) else [obj_type])
            if "JobPosting" in types:
                job_location = obj.get("jobLocation")
                raw = json.dumps(job_location or obj).lower()
                if "united states" in raw or '"us"' in raw or '"usa"' in raw:
                    score += 4
                    evidence.append("JobPosting location indicates US")

    return score, state, evidence


def analyze_usa_location(html, extra_text="", source_label="page", page_url=""):
    soup = BeautifulSoup(html, "html.parser")
    combined = f"{extra_text} {soup.get_text(' ', strip=True)}".strip()
    lower = combined.lower()
    evidence = []
    country = None
    state = None
    score = 0

    # Count a country mention once, even when the page says US, USA and United States.
    if ("united states" in lower or re.search(r"\busa\b|\bu\.s\.a?\.?\b", lower)):
        score += 4
        country = "United States"
        evidence.append(f"{source_label}: U.S. country mention (+4)")

    state = find_state_from_text(combined)
    if state:
        score += 3
        country = "United States"
        evidence.append(f"{source_label}: state {state} (+3)")
    elif any(re.search(pattern, lower) for pattern in (
        r"remote\s*[-–—,/|]?\s*(?:us|usa|united states)",
        r"(?:us|usa|united states)\s*[-–—,/|]?\s*remote",
    )):
        state = "US Remote"
        country = "United States"
        score += 2
        evidence.append(f"{source_label}: U.S. remote role (+2)")

    if re.search(r"\b\d{5}(?:-\d{4})?\b", combined):
        score += 1
        evidence.append(f"{source_label}: ZIP pattern (+1)")

    # An address on an official .edu page is stronger evidence than a country word alone.
    page_domain = get_domain(page_url) if page_url else ""
    if page_domain.endswith(".edu"):
        state_codes = "|".join(sorted(US_STATE_ABBREVIATIONS))
        address = re.search(
            rf"\b[A-Za-z][A-Za-z .'-]{{1,45}},\s*({state_codes})\s+\d{{5}}(?:-\d{{4}})?\b",
            combined,
        )
        if address:
            score = max(score, 8)
            state = address.group(1)
            country = "United States"
            evidence.append(f"{source_label}: .edu page with U.S. campus address (at least 8)")

    json_score, json_state, json_evidence = inspect_json_ld(soup)
    if json_score:
        score += 2
        evidence.extend(json_evidence)
    if json_state:
        state = json_state
        country = "United States"

    score = min(score, 10)
    if score > 0 and country is None:
        country = "Possible United States"
    return {"country": country, "state": state, "score": score, "evidence": evidence}


def merge_location_data(base, incoming):
    if incoming["score"] > base["score"]:
        base["country"] = incoming["country"] or base["country"]

    if incoming.get("state") and not base.get("state"):
        base["state"] = incoming["state"]

    if incoming.get("country") == "United States":
        base["country"] = "United States"

    base["score"] = min(10, max(base["score"], incoming["score"]))
    base.setdefault("evidence", []).extend(incoming.get("evidence", []))
    return base


def detect_work_arrangement(job_title="", html=""):
    """Classify explicit job arrangements without guessing from site boilerplate."""
    patterns = {
        "Remote": r"\b(?:remote|work(?:ing)? from home|wfh|telecommut(?:e|ing)|home[ -]based|distributed team)\b",
        "Hybrid": r"\b(?:hybrid|part(?:ly|ially) remote|split between (?:home|remote) and (?:the )?office)\b",
        "Onsite": r"\b(?:on[ -]?site|in[ -]?office|in[ -]?person|office[ -]based|on[ -]?premises)\b",
    }
    def classify(text):
        text = re.sub(patterns["Hybrid"], "HYBRID", text or "", flags=re.I)
        found = {name for name, pattern in patterns.items() if re.search(pattern, text, re.I)}
        return next(iter(found)) if len(found) == 1 else None

    if job_title:
        title = re.sub(patterns["Hybrid"], "HYBRID", job_title, flags=re.I)
        title_types = {name for name, pattern in patterns.items() if re.search(pattern, title, re.I)}
        if len(title_types) > 1:
            return None
        if title_types:
            return next(iter(title_types))
    if not html:
        return None
    soup = BeautifulSoup(html, "html.parser")
    for script in soup.find_all("script", type="application/ld+json"):
        if script.string and re.search(r'"jobLocationType"\s*:\s*"TELECOMMUTE"', script.string, re.I):
            return "Remote"
    text = soup.get_text(" ", strip=True)
    context = re.findall(
        r"\b(?:work (?:arrangement|location|model)|workplace|location type|this (?:role|position|job) is)\s*[:\-]?\s*([^.;]{0,75})",
        text, re.I,
    )
    return classify(" ".join(context))


# =========================================================
# SEARXNG SEARCH
# =========================================================


def search_searxng(query, page=1):
    if not check_searxng_timer():
        return []

    if USA_ONLY:
        search_query = f"{query} United States"

    else:
        search_query = query

    params = {
        "q": search_query,
        "format": "json",
        "language": "en-US",
        "pageno": page,
    }

    try:
        response = requests.get(
            SEARXNG_URL,
            params=params,
            timeout=30,
        )

        response.raise_for_status()

        data = response.json()

        return data.get("results", [])

    except requests.RequestException as error:
        print()
        print("Could not search SearXNG.")

        print(error)

        return []

    except ValueError:
        print()
        print("SearXNG did not return JSON.")

        return []


# =========================================================
# COMPANY SITE INSPECTION
# =========================================================


CAREER_STRONG_TERMS = [
    "careers",
    "career opportunities",
    "job openings",
    "open positions",
    "current openings",
    "join our team",
    "join us",
    "work with us",
    "work for us",
    "apply now",
]

CAREER_WEAK_TERMS = [
    "career",
    "jobs",
    "employment opportunities",
    "hiring",
    "opportunities",
]

CAREER_NEGATIVE_TERMS = [
    "unemployment benefits",
    "unemployment insurance",
    "file a claim",
    "benefits claim",
    "workforce services",
    "job seeker services",
]

ATS_DOMAINS = {
    "greenhouse.io",
    "lever.co",
    "myworkdayjobs.com",
    "workday.com",
    "icims.com",
    "jobvite.com",
    "smartrecruiters.com",
    "ashbyhq.com",
    "bamboohr.com",
    "paylocity.com",
}

SUPPORT_PAGE_TERMS = {
    "contact": 4,
    "about": 3,
    "privacy": 2,
    "terms": 2,
    "legal": 2,
    "imprint": 2,
}

SOCIAL_DOMAINS = {
    "facebook.com",
    "instagram.com",
    "linkedin.com",
    "twitter.com",
    "x.com",
    "youtube.com",
}

DIRECTORY_MARKETPLACE_DOMAINS = {
    "bark.com",
    "sortlist.com",
    "clutch.co",
    "goodfirms.co",
    "designrush.com",
    "yelp.com",
    "thumbtack.com",
    "expertise.com",
    "upcity.com",
    "agencyspotter.com",
}

DIRECTORY_TITLE_PATTERNS = [
    "find a ",
    "find an ",
    "best web design agencies",
    "best web designers",
    "top web design agencies",
    "top web designers",
    "reviews",
    "compare providers",
    "get quotes",
]


def is_directory_or_marketplace_result(domain, title="", html=""):
    if any(domain == item or domain.endswith("." + item) for item in DIRECTORY_MARKETPLACE_DOMAINS):
        return True

    title_lower = (title or "").lower()
    if any(pattern in title_lower for pattern in DIRECTORY_TITLE_PATTERNS):
        return True

    if html:
        soup = BeautifulSoup(html, "html.parser")
        page_title = soup.title.get_text(" ", strip=True).lower() if soup.title else ""
        heading_text = " ".join(
            heading.get_text(" ", strip=True)
            for heading in soup.find_all(["h1", "h2"], limit=8)
        ).lower()
        marker = f"{page_title} {heading_text}"
        directory_signals = [
            "service providers",
            "compare agencies",
            "agency directory",
            "business directory",
            "get free quotes",
            "find professionals",
        ]
        if sum(signal in marker for signal in directory_signals) >= 1:
            return True

    return False


def is_student_employment_overview(url, html):
    """Reject financial-aid guidance pages without a specific job posting."""
    soup = BeautifulSoup(html, "html.parser")
    headline = " ".join([soup.title.get_text(" ", strip=True) if soup.title else ""] +
                        [tag.get_text(" ", strip=True) for tag in soup.find_all("h1")]).lower()
    path = urlparse(url).path.lower()
    student_context = any(term in path for term in
                          ("financial-aid", "financialaid", "types-of-aid", "work-study"))
    general_heading = any(term in headline for term in
                          ("campus employment", "student employment", "federal work-study",
                           "work study information", "employment & internships"))
    has_job_posting = any(script.string and '"JobPosting"' in script.string
                          for script in soup.find_all("script", type="application/ld+json"))
    return student_context and general_heading and not has_job_posting


def score_career_page(url, html):
    if is_student_employment_overview(url, html):
        return {"score": 0, "evidence": ["Student employment overview, not an individual job posting"]}
    soup = BeautifulSoup(html, "html.parser")
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    headings = " ".join(
        heading.get_text(" ", strip=True)
        for heading in soup.find_all(["h1", "h2", "h3"])
    )
    visible_text = soup.get_text(" ", strip=True)
    searchable = f"{url} {title} {headings} {visible_text}".lower()

    score = 0
    evidence = []

    headline = f"{url} {title} {headings}".lower()
    headline_hits = [term for term in CAREER_STRONG_TERMS if term in headline]
    strong_hits = [term for term in CAREER_STRONG_TERMS if term in searchable]
    if headline_hits:
        score += min(4, 2 + len(headline_hits))
        evidence.append("career language in URL/title/heading: " + ", ".join(headline_hits[:3]))
    elif strong_hits:
        score += min(2, len(strong_hits))
        evidence.append("career language in page body: " + ", ".join(strong_hits[:3]))

    weak_hits = [term for term in CAREER_WEAK_TERMS if term in searchable]
    if weak_hits:
        score += 1
        evidence.append("supporting career language: " + ", ".join(weak_hits[:3]))

    job_like_links = 0
    apply_links = 0
    ats_links = 0

    for link in soup.find_all("a", href=True):
        text = link.get_text(" ", strip=True).lower()
        href = urljoin(url, link.get("href", ""))
        href_lower = href.lower()
        link_domain = get_domain(href) if is_valid_url(href) else ""

        if any(term in text or term in href_lower for term in ["job", "career", "position", "opening"]):
            job_like_links += 1

        if "apply" in text or "apply" in href_lower:
            apply_links += 1

        if any(link_domain == ats or link_domain.endswith("." + ats) for ats in ATS_DOMAINS):
            ats_links += 1

    if job_like_links:
        score += 1
        evidence.append(f"{job_like_links} job/career links")

    if apply_links:
        score += 2
        evidence.append(f"{apply_links} apply links")

    if ats_links:
        score += 2
        evidence.append(f"{ats_links} ATS links")

    negative_hits = [term for term in CAREER_NEGATIVE_TERMS if term in searchable]
    if negative_hits:
        score -= min(5, 2 * len(negative_hits))
        evidence.append("non-hiring language: " + ", ".join(negative_hits[:3]))

    return {
        "score": max(0, min(10, score)),
        "evidence": evidence,
    }


def discover_support_links(soup, base_url):
    base_domain = get_domain(base_url)
    ranked = []

    for link in soup.find_all("a", href=True):
        href = urljoin(base_url, link.get("href", ""))
        if not is_valid_url(href) or get_domain(href) != base_domain:
            continue

        marker = f"{link.get_text(' ', strip=True)} {href}".lower()
        weight = 0

        for term, term_weight in SUPPORT_PAGE_TERMS.items():
            if term in marker:
                weight = max(weight, term_weight)

        if weight:
            ranked.append((weight, href))

    ranked.sort(reverse=True)
    seen = set()
    results = []

    for _, url in ranked:
        if url not in seen:
            seen.add(url)
            results.append(url)

    return results[:MAX_DISCOVERY_PAGES]


def discover_sitemap_links(homepage):
    parsed = urlparse(homepage)
    root = f"{parsed.scheme}://{parsed.netloc}"
    sitemap_url = urljoin(root + "/", "sitemap.xml")

    try:
        response = requests.get(sitemap_url, headers=HEADERS, timeout=TIMEOUT)
        if not response.ok or len(response.content) > MAX_HTML_SIZE:
            return []
    except requests.RequestException:
        return []

    candidates = []
    for match in re.findall(r"<loc>\s*(.*?)\s*</loc>", response.text, flags=re.I):
        lowered = match.lower()
        if any(term in lowered for term in [
            "career", "jobs", "join", "employment", "contact", "about", "privacy", "terms", "legal"
        ]):
            candidates.append(match.strip())

    return candidates[:MAX_DISCOVERY_PAGES]


def discover_robots_links(homepage):
    parsed = urlparse(homepage)
    root = f"{parsed.scheme}://{parsed.netloc}"
    robots_url = urljoin(root + "/", "robots.txt")

    if not update_existing_mode and not check_searxng_timer():
        return []

    try:
        time.sleep(REQUEST_DELAY)
        response = requests.get(robots_url, headers=HEADERS, timeout=TIMEOUT)
        if not response.ok or len(response.content) > 500_000:
            return []
    except requests.RequestException:
        return []

    discovered = []
    discovery_terms = [
        "career", "jobs", "employment", "join",
        "contact", "about", "privacy", "terms", "legal",
    ]

    for raw_line in response.text.splitlines():
        line = raw_line.strip()
        if not line or ":" not in line:
            continue

        directive, value = line.split(":", 1)
        directive = directive.strip().lower()
        value = value.strip()

        if directive == "sitemap" and is_valid_url(value):
            discovered.append(value)
            continue

        if directive not in {"allow", "disallow"}:
            continue

        lowered = value.lower()
        if any(term in lowered for term in discovery_terms):
            candidate = urljoin(root + "/", value)
            if is_valid_url(candidate):
                discovered.append(candidate)

    seen = set()
    return [url for url in discovered if not (url in seen or seen.add(url))][:MAX_DISCOVERY_PAGES]


def find_external_company_site(soup, base_url):
    base_domain = get_domain(base_url)
    candidates = []

    for link in soup.find_all("a", href=True):
        text = link.get_text(" ", strip=True).lower()
        href = urljoin(base_url, link.get("href", ""))

        if not is_valid_url(href):
            continue

        domain = get_domain(href)
        if not domain or domain == base_domain:
            continue

        if any(domain == social or domain.endswith("." + social) for social in SOCIAL_DOMAINS):
            continue

        if any(domain == ats or domain.endswith("." + ats) for ats in ATS_DOMAINS):
            continue

        if is_blocked_domain(domain) or has_blocked_country_domain(domain):
            continue

        weight = 0
        if any(term in text for term in ["website", "visit website", "company website", "official site"]):
            weight += 4
        if "http" in link.get("href", ""):
            weight += 1

        if weight:
            candidates.append((weight, href))

    if not candidates:
        return None

    candidates.sort(reverse=True)
    return candidates[0][1]


def inspect_company_site(homepage, search_title, domain):
    response = safe_request(homepage)

    if response is None:
        return {
            "company_name": domain,
            "career_candidates": [],
            "location": {"country": None, "state": None, "score": 0, "evidence": []},
            "official_site": None,
            "landing_html": "",
        }

    soup = BeautifulSoup(response.text, "html.parser")
    company_name = extract_company_name(soup, search_title, domain)

    # Search-result title and landing page are useful location evidence too.
    location = analyze_usa_location(
        response.text,
        extra_text=search_title,
        source_label="search/landing page",
        page_url=response.url,
    )

    career_candidates = []
    seen = set()

    for link in soup.find_all("a", href=True):
        link_text = link.get_text(" ", strip=True).lower()
        href = link.get("href", "")
        href_lower = href.lower()

        if any(term in link_text or term in href_lower for term in CAREER_STRONG_TERMS + CAREER_WEAK_TERMS):
            full_url = urljoin(homepage, href)
            if is_valid_url(full_url) and full_url not in seen:
                seen.add(full_url)
                career_candidates.append(full_url)

    support_links = discover_support_links(soup, homepage)
    sitemap_links = discover_sitemap_links(homepage)
    robots_links = discover_robots_links(homepage)

    for support_url in support_links + sitemap_links + robots_links:
        support_response = safe_request(support_url)
        if support_response is None:
            continue

        support_location = analyze_usa_location(
            support_response.text,
            source_label=f"support page {support_url}",
            page_url=support_response.url,
        )
        location = merge_location_data(location, support_location)

        support_soup = BeautifulSoup(support_response.text, "html.parser")
        for link in support_soup.find_all("a", href=True):
            marker = f"{link.get_text(' ', strip=True)} {link.get('href', '')}".lower()
            if any(term in marker for term in CAREER_STRONG_TERMS + CAREER_WEAK_TERMS):
                full_url = urljoin(support_url, link.get("href", ""))
                if is_valid_url(full_url) and full_url not in seen:
                    seen.add(full_url)
                    career_candidates.append(full_url)

    official_site = find_external_company_site(soup, homepage)

    return {
        "company_name": company_name,
        "career_candidates": career_candidates,
        "location": location,
        "official_site": official_site,
        "landing_html": response.text,
    }


def choose_best_career_page(candidate_urls):
    best = None

    for candidate_url in candidate_urls[:MAX_DISCOVERY_PAGES]:
        response = safe_request(candidate_url)
        if response is None:
            continue

        career_data = score_career_page(candidate_url, response.text)
        if is_student_employment_overview(candidate_url, response.text):
            print(f"Skipping student employment overview: {candidate_url}")
            continue
        location_data = analyze_usa_location(
            response.text,
            source_label=f"career page {candidate_url}",
            page_url=response.url,
        )

        candidate = {
            "url": candidate_url,
            "career": career_data,
            "location": location_data,
            "html": response.text,
        }

        if best is None or candidate["career"]["score"] > best["career"]["score"]:
            best = candidate

    return best


# =========================================================
# UPDATE EXISTING RESULTS
# =========================================================


def update_existing_results(company_ids=None):
    """Recheck existing rows without starting Docker or the search engine."""
    global update_existing_mode
    update_existing_mode = True
    print()
    print("================================")
    print("     UPDATE EXISTING RESULTS")
    print("================================")
    print()

    database = connect_database()
    if database is None:
        return

    if not ensure_database_schema(database):
        database.close()
        return

    cursor = None
    try:
        cursor = database.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT
                id,
                name,
                career_job_title,
                domain,
                career_url,
                source_url,
                country,
                state,
                usa_credibility,
                career_credibility,
                work_arrangement,
                listing_skills,
                source_type,
                is_kept,
                job_open_status
            FROM companies
            WHERE is_rejected = 0
            """ + (" AND id IN (" + ",".join(["%s"] * len(company_ids)) + ")" if company_ids is not None else "") + " ORDER BY date_found ASC",
            tuple(company_ids) if company_ids is not None else (),
        )
        companies = cursor.fetchall()
    except Error as error:
        print("Could not read existing companies for update.")
        print(error)
        if cursor is not None:
            cursor.close()
        database.close()
        return
    finally:
        if cursor is not None:
            cursor.close()

    print(f"Found {len(companies)} existing results to verify.")

    updated = 0
    failed = 0
    remote_rows = {row.get("source_url") for row in companies if row.get("source_type") == "Remote OK"}
    remote_feed = None
    if remote_rows:
        try:
            remote_feed = {str(item.get("url") or item.get("apply_url")): item for item in fetch_remote_ok_jobs()}
        except (requests.RequestException, ValueError, TypeError) as error:
            print(f"Remote OK refresh unavailable: {error}")

    for index, company in enumerate(companies, start=1):
        company_id = company["id"]
        career_url = company.get("career_url")
        source_url = company.get("source_url")
        search_title = company.get("career_job_title") or company.get("name") or ""
        work_arrangement = detect_work_arrangement(search_title)
        skills = None

        print()
        print("=" * 60)
        print(f"Updating {index}/{len(companies)}: {company.get('name') or company.get('domain')}")
        if company.get("source_type") == "Remote OK":
            item = (remote_feed or {}).get(source_url)
            if item:
                try:
                    new_skills = listing_skills(item.get("description") or "")
                    feed_location = str(item.get("location") or "")[:100] or None
                    location_changed = company.get("state") != feed_location
                    status_changed = company.get("job_open_status") != "Open"
                    with database.cursor() as feed_cursor:
                        feed_cursor.execute(
                            """UPDATE companies SET listing_skills = %s, state = %s,
                               job_open_status = 'Open', last_checked = %s,
                               result_updated_at = CASE WHEN %s THEN %s ELSE result_updated_at END
                               WHERE id = %s""",
                            (json.dumps(new_skills), feed_location, datetime.now(timezone.utc),
                             location_changed or status_changed, datetime.now(timezone.utc), company_id),
                        )
                    database.commit()
                    updated += 1
                    print("Remote OK listing still appears in the recent feed.")
                except Error as error:
                    database.rollback()
                    failed += 1
                    print(f"Could not refresh Remote OK listing: {error}")
            elif remote_feed is not None:
                try:
                    with database.cursor() as feed_cursor:
                        feed_cursor.execute(
                            """UPDATE companies SET job_open_status = 'Unknown', last_checked = %s,
                               result_updated_at = CASE WHEN job_open_status <> 'Unknown' THEN %s ELSE result_updated_at END
                               WHERE id = %s""",
                            (datetime.now(timezone.utc), datetime.now(timezone.utc), company_id),
                        )
                    database.commit()
                    updated += 1
                    print("Not in the recent feed; listing status is Unknown until verified at the source.")
                except Error as error:
                    database.rollback()
                    failed += 1
                    print(f"Could not update Remote OK listing status: {error}")
            else:
                failed += 1
                print("Remote OK feed unavailable; status left unchanged.")
            continue

        career_credibility = 0
        job_open_status = company.get("job_open_status") or "Open"
        location_data = {
            "country": None,
            "state": None,
            "score": 0,
            "evidence": [],
        }

        if source_url and is_valid_url(source_url):
            source_data = inspect_company_site(
                homepage=source_url,
                search_title=search_title,
                domain=company.get("domain") or get_domain(source_url),
            )
            location_data = merge_location_data(location_data, source_data["location"])
            work_arrangement = work_arrangement or detect_work_arrangement("", source_data.get("landing_html", ""))

        if career_url and is_valid_url(career_url):
            response = safe_request(career_url)
            if response is not None:
                skills = listing_skills(response.text)
                work_arrangement = work_arrangement or detect_work_arrangement("", response.text)
                career_data = score_career_page(career_url, response.text)
                career_credibility = career_data["score"]
                if career_credibility >= CAREER_CREDIBILITY_THRESHOLD:
                    job_open_status = "Open"
                elif career_credibility <= 2:
                    job_open_status = "Closed"
                career_location = analyze_usa_location(
                    response.text,
                    extra_text=search_title,
                    source_label=f"career page {career_url}",
                    page_url=response.url,
                )
                location_data = merge_location_data(location_data, career_location)

                print(f"Career credibility: {career_credibility}")
                for reason in career_data.get("evidence", []):
                    print(f"  Evidence: {reason}")
            else:
                job_open_status = "Closed"
                print("Career page could not be loaded; marking job Closed.")
        else:
            job_open_status = "Closed"
            print("No usable career URL; marking job Closed.")

        if location_data["score"] == 0 and not location_data["evidence"]:
            location_data["country"] = company.get("country")
            location_data["state"] = company.get("state")
            location_data["score"] = company.get("usa_credibility") or 0

        if not work_arrangement and not source_url and not career_url:
            work_arrangement = company.get("work_arrangement")

        update_cursor = None
        try:
            changed = any((
                company.get("work_arrangement") != work_arrangement,
                company.get("career_credibility") != career_credibility,
                (company.get("job_open_status") or "Open") != job_open_status,
                company.get("country") != location_data.get("country"),
                company.get("state") != location_data.get("state"),
                (company.get("usa_credibility") or 0) != location_data.get("score", 0),
            ))
            checked_at = datetime.now(timezone.utc)
            update_cursor = database.cursor()
            update_cursor.execute(
                """
                UPDATE companies
                SET
                    career_credibility = %s,
                    job_open_status = %s,
                    source_type = COALESCE(NULLIF(source_type, ''), 'SearXNG'),
                    country = %s,
                    state = %s,
                    usa_credibility = %s,
                    work_arrangement = %s,
                    listing_skills = COALESCE(%s, listing_skills),
                    last_checked = %s,
                    result_updated_at = CASE WHEN %s THEN %s ELSE result_updated_at END
                WHERE id = %s
                """,
                (
                    career_credibility,
                    job_open_status,
                    location_data.get("country"),
                    location_data.get("state"),
                    location_data.get("score", 0),
                    work_arrangement,
                    json.dumps(skills) if skills is not None else None,
                    checked_at,
                    changed,
                    checked_at,
                    company_id,
                ),
            )
            database.commit()
            updated += 1
            print("Existing row updated in place.")
        except Error as error:
            database.rollback()
            failed += 1
            print("Could not update this existing row.")
            print(error)
        finally:
            if update_cursor is not None:
                update_cursor.close()

    database.close()

    print()
    print("=" * 60)
    print("UPDATE COMPLETE")
    print(f"Rows updated: {updated}")
    print(f"Rows failed: {failed}")




# =========================================================
# CITY / DISTANCE FILTERING
# =========================================================

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
JOB_FINDER_VERSION = "1.1.38"
_last_nominatim_request = 0.0
_failed_geocode_queries = set()


def _normalize_geocode_query(value):
    return re.sub(r"\s+", " ", (value or "").strip()).lower()[:255]


def geocode_location(database, query):
    """Geocode once with public Nominatim, then reuse the MySQL cache."""
    global _last_nominatim_request
    key = _normalize_geocode_query(query)
    if not key:
        return None
    if key in _failed_geocode_queries:
        return None

    cursor = database.cursor(dictionary=True)
    try:
        cursor.execute("SELECT latitude, longitude, display_name FROM geocode_cache WHERE query_text = %s", (key,))
        cached = cursor.fetchone()
        if cached:
            return {"lat": float(cached["latitude"]), "lon": float(cached["longitude"]), "display_name": cached.get("display_name")}
    finally:
        cursor.close()

    elapsed = time.monotonic() - _last_nominatim_request
    if elapsed < 1.05:
        time.sleep(1.05 - elapsed)

    try:
        response = requests.get(
            NOMINATIM_URL,
            params={"q": query, "format": "jsonv2", "limit": 1, "countrycodes": "us"},
            headers={"User-Agent": f"JobFinder/{JOB_FINDER_VERSION} (https://github.com/jltkerig/web-scraper)"},
            timeout=15,
        )
        _last_nominatim_request = time.monotonic()
        response.raise_for_status()
        results = response.json()
        if not results:
            _failed_geocode_queries.add(key)
            return None
        result = results[0]
        lat, lon = float(result["lat"]), float(result["lon"])
        display_name = result.get("display_name", "")[:1000]
        cursor = database.cursor()
        try:
            cursor.execute(
                "INSERT INTO geocode_cache (query_text, latitude, longitude, display_name) VALUES (%s, %s, %s, %s) ON DUPLICATE KEY UPDATE latitude=VALUES(latitude), longitude=VALUES(longitude), display_name=VALUES(display_name)",
                (key, lat, lon, display_name),
            )
            database.commit()
        finally:
            cursor.close()
        return {"lat": lat, "lon": lon, "display_name": display_name}
    except (requests.RequestException, ValueError, KeyError) as error:
        _failed_geocode_queries.add(key)
        print(f"Geocoding skipped for {query}: {error}")
        return None


def haversine_miles(lat1, lon1, lat2, lon2):
    radius = 3958.7613
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return radius * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def extract_job_city(html, fallback_text=""):
    soup = BeautifulSoup(html or "", "html.parser")
    json_objects = []
    for tag in soup.find_all("script", attrs={"type": "application/ld+json"}):
        try:
            data = json.loads(tag.string or tag.get_text() or "null")
        except (json.JSONDecodeError, TypeError):
            continue
        json_objects.append(data)

    def iter_objects(value):
        if isinstance(value, dict):
            yield value
            for child in value.values():
                yield from iter_objects(child)
        elif isinstance(value, list):
            for child in value:
                yield from iter_objects(child)

    # Prefer JobPosting.jobLocation over unrelated company/legal addresses.
    for data in json_objects:
        for obj in iter_objects(data):
            obj_type = obj.get("@type")
            types = obj_type if isinstance(obj_type, list) else [obj_type]
            if "JobPosting" not in types:
                continue
            locations = obj.get("jobLocation") or obj.get("applicantLocationRequirements")
            for location in iter_objects(locations):
                address = location.get("address") if isinstance(location, dict) else None
                if isinstance(address, dict):
                    locality = str(address.get("addressLocality", "")).strip()
                    region = str(address.get("addressRegion", "")).strip()
                    if locality:
                        return locality, region

    # Fall back to structured addresses only if no JobPosting location exists.
    for data in json_objects:
        for obj in iter_objects(data):
            address = obj.get("address") if isinstance(obj, dict) else None
            if isinstance(address, dict):
                locality = str(address.get("addressLocality", "")).strip()
                region = str(address.get("addressRegion", "")).strip()
                if locality:
                    return locality, region

    combined = f"{fallback_text} {soup.get_text(' ', strip=True)}"
    match = re.search(r"\b([A-Z][A-Za-z .'-]{1,45}),\s*([A-Z]{2})\b", combined)
    if match and match.group(2) in US_STATE_ABBREVIATIONS:
        return match.group(1).strip(), match.group(2)
    return None, None


def prepare_city_targets(database, state, cities):
    targets = []
    allowed = {10, 15, 20, 30, 50}
    for item in cities or []:
        city = str(item.get("city", "")).strip()
        try:
            radius = int(item.get("radius", 50))
        except (TypeError, ValueError):
            radius = 50
        if not city or radius not in allowed:
            continue
        if city.casefold() in US_STATES:
            continue  # A state name is a statewide target, not a city-radius center.
        query = city if "," in city else f"{city}, {state}"
        point = geocode_location(database, query)
        if point:
            targets.append({"city": city, "radius": radius, **point})
            print(f"City radius: {city} — {radius} miles")
    return targets


def distance_to_city_targets(database, html, fallback_text, state, targets):
    if not targets:
        return True, None, None, None, None
    city, detected_state = extract_job_city(html, fallback_text)
    if not city:
        return False, None, None, None, None
    query_state = detected_state or state
    point = geocode_location(database, f"{city}, {query_state}")
    if not point:
        return False, city, None, None, None
    best_distance = None
    for target in targets:
        distance = haversine_miles(point["lat"], point["lon"], target["lat"], target["lon"])
        if best_distance is None or distance < best_distance:
            best_distance = distance
        if distance <= target["radius"]:
            return True, city, point["lat"], point["lon"], round(distance, 2)
    return False, city, point["lat"], point["lon"], round(best_distance, 2) if best_distance is not None else None

# =========================================================
# MAIN
# =========================================================


def main(job_title=None, state=None, cities_json=None, max_new=None):
    global MAX_SEARCH_RESULTS
    if max_new is not None:
        MAX_SEARCH_RESULTS = max_new
    print()
    print("================================")
    print("       PERSONAL JOB FINDER")
    print("================================")
    print()

    database = connect_database()

    if database is None:
        return

    if not ensure_database_schema(database):
        database.close()
        return

    print()
    print("Connected to job_finder database.")

    print()

    if not job_title:
        job_title = input("Job title: ").strip()

    if not state:
        state = input("State: ").strip()

    job_titles = []
    seen_job_titles = set()
    for raw_title in job_title.split(","):
        cleaned_title = raw_title.strip()
        key = cleaned_title.lower()
        if cleaned_title and key not in seen_job_titles:
            seen_job_titles.add(key)
            job_titles.append(cleaned_title)

    if not job_titles:
        print("No valid job titles were provided.")
        database.close()
        return

    try:
        cities = json.loads(cities_json or "[]")
        if not isinstance(cities, list):
            cities = []
    except json.JSONDecodeError:
        cities = []
    statewide_states = {US_STATES[str(item.get("city", "")).strip().casefold()]
                        for item in cities if isinstance(item, dict)
                        and str(item.get("city", "")).strip().casefold() in US_STATES}
    city_targets = prepare_city_targets(database, state, cities)
    requested_cities = [item for item in cities if isinstance(item, dict)
                        and str(item.get("city", "")).strip()
                        and str(item.get("city", "")).strip().casefold() not in US_STATES]
    if requested_cities and not city_targets:
        print("ERROR: None of the selected cities could be geocoded. The search was stopped so the mileage filter would not be silently ignored.")
        database.close()
        raise SystemExit(2)
    if requested_cities and len(city_targets) < len(requested_cities):
        print(f"Warning: {len(requested_cities) - len(city_targets)} selected city/cities could not be geocoded and were skipped.")
    if statewide_states:
        print("Statewide locations: " + ", ".join(sorted(statewide_states)))

    print()
    print(f'Job titles: "{", ".join(job_titles)}"')
    print(f'State: "{state}"')
    print(f'Searching {len(job_titles)} job title(s).')

    visited_domains = set()
    seen_result_urls = set()
    results_checked = 0
    candidate_number = 0
    pages_checked = 0


    websites_checked = 0
    career_sites_found = 0
    companies_saved = 0
    passed_count = 0
    blocked_sites = 0
    blocked_countries = 0
    duplicates = 0
    existing_companies = 0
    no_career_page = 0

    # Remote OK publishes a bounded recent feed. Its URL remains the View link
    # to satisfy the provider's attribution and to avoid inventing employer URLs.
    if not is_blocked_domain("remoteok.com"):
        print("Checking Remote OK for matching remote listings...")
        try:
            remote_jobs = list(matching_remote_ok_jobs(fetch_remote_ok_jobs(), job_titles, USA_ONLY))
            print(f"Remote OK matches: {len(remote_jobs)}")
            remote_saved = 0
            for job in remote_jobs:
                if companies_saved >= MAX_SEARCH_RESULTS or remote_saved >= max(1, MAX_SEARCH_RESULTS // 3):
                    break
                candidate_number += 1
                results_checked += 1
                print(f"Checking result {candidate_number}: {job['title'][:75]} (Remote OK)")
                if not job["name"] or job["name"].casefold() in BLOCKED_COMPANIES:
                    continue
                inserted = save_company(
                    database, job["name"], job["title"], 6, "remoteok.com",
                    job["url"], job["url"], "United States" if job["usa_score"] == 6 else None,
                    job["location"][:100] or None, job["usa_score"], work_arrangement="Remote",
                    skills=listing_skills(job["html"]), source_type="Remote OK",
                )
                passed_count += 1
                print(f"Passed validation: {passed_count}")
                if inserted:
                    companies_saved += 1
                    remote_saved += 1
                    print(f"Saved viable company ({companies_saved}/{MAX_SEARCH_RESULTS}).")
                else:
                    existing_companies += 1
        except (requests.RequestException, ValueError, TypeError) as error:
            print(f"Remote OK unavailable; continuing web search: {error}")

    # The remote feed works without Docker. Search it first so a Docker failure
    # does not prevent independent API results from being saved.
    if not start_docker_desktop():
        database.close()
        return
    if not start_searxng():
        database.close()
        stop_docker_desktop()
        return

    search_queries = []
    city_names = [item.get("city", "").strip() for item in cities if isinstance(item, dict)
                  and item.get("city") and item.get("city", "").strip().casefold() not in US_STATES]
    for title in job_titles:
        title_queries = []
        for city_name in city_names:
            title_queries.extend([
                f'"{title}" "{city_name}" "{state}" jobs',
                f'"{title}" "{city_name}" careers',
            ])
        title_queries.extend([
            f"{title} {state}".strip(),
            f'"{title}" "{state}" careers',
            f'"{title}" "{state}" jobs',
            f'{title} careers {state}',
            f'{title} hiring {state}',
        ])
        for candidate_query in title_queries:
            if candidate_query not in search_queries:
                search_queries.append(candidate_query)


    for search_query in search_queries:
        if companies_saved >= MAX_SEARCH_RESULTS or not check_searxng_timer():
            break

        print()
        print(f'Search variation: "{search_query}"')

        empty_or_repeating_pages = 0

        for page in range(1, MAX_SEARCH_PAGES + 1):
            if companies_saved >= MAX_SEARCH_RESULTS or not check_searxng_timer():
                break

            results = search_searxng(search_query, page)

            if not results:
                empty_or_repeating_pages += 1
                if empty_or_repeating_pages >= 2:
                    break
                continue

            new_results = [
                result
                for result in results
                if result.get("url")
                and result.get("url") not in seen_result_urls
            ]

            if not new_results:
                empty_or_repeating_pages += 1
                if empty_or_repeating_pages >= 2:
                    break
                continue

            empty_or_repeating_pages = 0
            seen_result_urls.update(result.get("url") for result in new_results)
            pages_checked += 1
            results_checked += len(new_results)
            print(f"Checking search page {page} ({len(new_results)} new results)...")

            for result in new_results:
                if companies_saved >= MAX_SEARCH_RESULTS or not check_searxng_timer():
                    break
                candidate_number += 1
                title = result.get(
                    "title",
                    "Unknown Company",
                )

                print(f"Checking result {candidate_number}: {re.sub(r'\s+', ' ', str(title))[:75]}")

                url = result.get(
                    "url",
                    "",
                )

                if not url:
                    continue

                if not is_valid_url(url):
                    continue

                domain = get_domain(url)

                if not domain:
                    continue

                if is_blocked_domain(domain):
                    blocked_sites += 1

                    print("Skipping aggregator: " f"{domain}")

                    continue

                if has_blocked_country_domain(domain):
                    blocked_countries += 1

                    print("Skipping non-US domain: " f"{domain}")

                    continue

                if domain in visited_domains:
                    duplicates += 1
                    continue

                visited_domains.add(domain)

                websites_checked += 1

                print()
                print("=" * 60)
                print(title)
                print(url)
                print("Checking website...")

                original_source_url = url

                site_data = inspect_company_site(
                    homepage=url,
                    search_title=title,
                    domain=domain,
                )

                source_is_directory = is_directory_or_marketplace_result(
                    domain,
                    title=title,
                    html=site_data.get("landing_html", ""),
                )

                if source_is_directory:
                    print("Directory/marketplace result detected; using it only as a discovery lead.")

                # Directory/profile pages can point us to the actual company site.
                resolved_official_employer = False

                if site_data.get("official_site"):
                    official_url = site_data["official_site"]
                    official_domain = get_domain(official_url)

                    print(f"Possible official company site: {official_url}")

                    if official_domain and official_domain != domain:
                        official_data = inspect_company_site(
                            homepage=official_url,
                            search_title=title,
                            domain=official_domain,
                        )

                        if official_data["career_candidates"]:
                            site_data = official_data
                            domain = official_domain
                            url = official_url
                            resolved_official_employer = True

                if source_is_directory and not resolved_official_employer:
                    print("Skipping listing site: no verified official employer careers page was found.")
                    no_career_page += 1
                    continue

                company_name = site_data["company_name"]
                if company_name.strip().casefold() in BLOCKED_COMPANIES:
                    print(f"Skipping blocked company: {company_name}")
                    continue
                career_candidates = site_data["career_candidates"]
                location_data = site_data["location"]

                print(f"Company: {company_name}")

                if not career_candidates:
                    no_career_page += 1
                    print("No career candidates found after deeper inspection.")
                    continue

                print("Possible career pages:")
                for career_link in career_candidates:
                    print(f"  {career_link}")

                best_career = choose_best_career_page(career_candidates)

                if best_career is None:
                    no_career_page += 1
                    print("Career candidates could not be loaded.")
                    continue

                career_sites_found += 1
                primary_career_url = best_career["url"]
                work_arrangement = detect_work_arrangement(title, best_career.get("html", ""))
                print(f"  Work arrangement: {work_arrangement or 'Unknown'}")
                career_credibility = best_career["career"]["score"]
                location_data = merge_location_data(
                    location_data,
                    best_career["location"],
                )

                print()
                print("Career validation:")
                print(f"  Career credibility: {career_credibility}")
                for reason in best_career["career"]["evidence"]:
                    print(f"  Evidence: {reason}")

                print()
                print("Location information:")
                print("  Country: " f"{location_data['country'] or 'Unknown'}")
                print("  State: " f"{location_data['state'] or 'Unknown'}")
                print("  USA credibility: " f"{location_data['score']}")
                for reason in location_data.get("evidence", []):
                    print(f"  Evidence: {reason}")

                listing_state = str(location_data.get("state") or "").strip()
                listing_code = US_STATES.get(listing_state.casefold(), listing_state.upper())
                statewide_match = listing_code in statewide_states
                within_radius, detected_city, job_lat, job_lon, distance_miles = True, None, None, None, None
                if statewide_match:
                    print(f"  Statewide match: {listing_code}")
                elif city_targets:
                    within_radius, detected_city, job_lat, job_lon, distance_miles = distance_to_city_targets(
                        database, best_career.get("html", ""), title, state, city_targets
                    )
                    if detected_city:
                        print(f"  City: {detected_city}")
                        if distance_miles is not None:
                            print(f"  Nearest saved city: {distance_miles:.1f} miles")
                elif statewide_states:
                    within_radius = False
                if not within_radius:
                    print("Skipping: listing does not match a selected state or city radius.")
                    continue

                is_viable = career_credibility >= CAREER_CREDIBILITY_THRESHOLD
                if USA_ONLY:
                    is_viable = (
                        is_viable
                        and location_data["score"] >= USA_CREDIBILITY_THRESHOLD
                    )

                inserted = save_company(
                    connection=database,
                    name=company_name,
                    career_job_title=title,
                    career_credibility=career_credibility,
                    domain=domain,
                    career_url=primary_career_url,
                    source_url=original_source_url,
                    country=location_data["country"],
                    state=location_data["state"],
                    usa_credibility=location_data["score"],
                    work_arrangement=work_arrangement,
                    skills=listing_skills(best_career.get("html", "")),
                    city=detected_city,
                    latitude=job_lat,
                    longitude=job_lon,
                    distance_miles=distance_miles,
                )

                if not is_viable:
                    print(
                        "Saved for review, but not counted as viable "
                        f"(career >= {CAREER_CREDIBILITY_THRESHOLD}, "
                        f"USA >= {USA_CREDIBILITY_THRESHOLD})."
                    )
                    continue

                passed_count += 1
                print(f"Passed validation: {passed_count}")
                if inserted:
                    companies_saved += 1
                    print(
                        f"Saved viable company "
                        f"({companies_saved}/{MAX_SEARCH_RESULTS})."
                    )
                else:
                    existing_companies += 1
                    print(
                        "Viable company already exists in database; "
                        "continuing search."
                    )

    if companies_saved < MAX_SEARCH_RESULTS:
        print()
        print(
            f"Search exhausted after saving {companies_saved}/"
            f"{MAX_SEARCH_RESULTS} viable companies."
        )

    database.close()

    print()
    print("=" * 60)
    print("SEARCH COMPLETE")
    print()

    print(f"Search results checked: {results_checked}")
    print(f"Search pages checked: {pages_checked}")

    print(f"Aggregators skipped: " f"{blocked_sites}")

    print(f"Non-US domains skipped: " f"{blocked_countries}")

    print(f"Duplicates skipped: " f"{duplicates}")

    print(f"Existing companies updated: " f"{existing_companies}")

    print(f"Websites checked: " f"{websites_checked}")

    print(f"No career page: " f"{no_career_page}")

    print(f"Career sites found: " f"{career_sites_found}")

    print(f"Database saves: " f"{companies_saved}")

    stop_searxng()
    stop_docker_desktop()


def parse_arguments():
    parser = argparse.ArgumentParser(description="Personal Job Finder")
    parser.add_argument("--update-existing", action="store_true")
    parser.add_argument("--update-ids", default=None)
    parser.add_argument("--max-new", type=int, choices=range(1, 11), default=None)
    parser.add_argument("--job-title", default=None)
    parser.add_argument("--state", default=None)
    parser.add_argument("--cities-json", default=None)
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_arguments()

    if args.update_existing:
        update_existing_results([int(value) for value in args.update_ids.split(",") if value.isdecimal()] if args.update_ids is not None else None)
    else:
        main(job_title=args.job_title, state=args.state, cities_json=args.cities_json, max_new=args.max_new)
