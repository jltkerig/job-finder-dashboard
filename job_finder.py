import json
import os
import re
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

import mysql.connector
import requests
from bs4 import BeautifulSoup
from mysql.connector import Error
from dotenv import load_dotenv

# =========================================================
# FILES
# =========================================================

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

SETTINGS_FILE = BASE_DIR / "settings.json"
BLOCKED_DOMAINS_FILE = BASE_DIR / "blocked_domains.txt"
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

BLOCKED_COUNTRY_DOMAINS = load_domain_file(BLOCKED_COUNTRY_DOMAINS_FILE)


# =========================================================
# PROGRAM SETTINGS
# =========================================================

SEARXNG_URL = "http://localhost:8080/search"
SEARXNG_CONTAINER = "searxng"

SEARXNG_MAX_RUNTIME = settings["searxng_timeout_minutes"] * 60

MAX_SEARCH_RESULTS = settings["max_search_results"]
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
    domain,
    career_url,
    source_url,
    country,
    state,
    usa_confidence,
):
    now = datetime.now(timezone.utc)

    cursor = None

    try:
        cursor = connection.cursor()

        sql = """
        INSERT INTO companies
        (
            name,
            domain,
            career_url,
            source_url,
            country,
            state,
            usa_confidence,
            date_found,
            last_checked
        )
        VALUES
        (
            %s,
            %s,
            %s,
            %s,
            %s,
            %s,
            %s,
            %s,
            %s
        )

        ON DUPLICATE KEY UPDATE
            name = VALUES(name),
            career_url = VALUES(career_url),
            source_url = VALUES(source_url),
            country = VALUES(country),
            state = VALUES(state),
            usa_confidence = VALUES(usa_confidence),
            last_checked = VALUES(last_checked)
        """

        cursor.execute(
            sql,
            (
                name,
                domain,
                career_url,
                source_url,
                country,
                state,
                usa_confidence,
                now,
                now,
            ),
        )

        connection.commit()

        return True

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
    if not check_searxng_timer():
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

    for (
        state_name,
        abbreviation,
    ) in US_STATES.items():
        if state_name in text_lower:
            return abbreviation

    state_pattern = (
        r"\b(" + "|".join(sorted(US_STATE_ABBREVIATIONS)) + r")\s+\d{5}(?:-\d{4})?\b"
    )

    match = re.search(
        state_pattern,
        text,
    )

    if match:
        return match.group(1)

    return None


def inspect_json_ld(soup):
    score = 0
    state = None

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

        raw = json.dumps(data).lower()

        if (
            '"addresscountry": "us"' in raw
            or '"addresscountry": "usa"' in raw
            or '"addresscountry": "united states"' in raw
        ):
            score += 8

        for abbreviation in US_STATE_ABBREVIATIONS:
            if f'"addressregion": "{abbreviation.lower()}"' in raw:
                state = abbreviation
                score += 4
                break

    return score, state


def analyze_usa_location(html):
    soup = BeautifulSoup(
        html,
        "html.parser",
    )

    text = soup.get_text(
        " ",
        strip=True,
    )

    text_lower = text.lower()

    score = 0
    country = None
    state = None

    if "united states" in text_lower:
        score += 5
        country = "United States"

    if re.search(
        r"\busa\b",
        text_lower,
    ):
        score += 4
        country = "United States"

    if re.search(
        r"\bu\.s\.\b",
        text_lower,
    ):
        score += 4
        country = "United States"

    state = find_state_from_text(text)

    if state:
        score += 3

    if re.search(
        r"\b\d{5}(?:-\d{4})?\b",
        text,
    ):
        score += 2

    json_score, json_state = inspect_json_ld(soup)

    score += json_score

    if json_state:
        state = json_state

    if score > 0:
        country = country or "Possible United States"

    return {
        "country": country,
        "state": state,
        "score": score,
    }


# =========================================================
# SEARXNG SEARCH
# =========================================================


def search_searxng(query):
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
    }

    try:
        response = requests.get(
            SEARXNG_URL,
            params=params,
            timeout=30,
        )

        response.raise_for_status()

        data = response.json()

        return data.get(
            "results",
            [],
        )[:MAX_SEARCH_RESULTS]

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


def inspect_company_site(
    homepage,
    search_title,
    domain,
):
    response = safe_request(homepage)

    if response is None:
        return {
            "company_name": domain,
            "career_links": [],
        }

    soup = BeautifulSoup(
        response.text,
        "html.parser",
    )

    company_name = extract_company_name(
        soup,
        search_title,
        domain,
    )

    keywords = [
        "career",
        "careers",
        "jobs",
        "job openings",
        "employment",
        "employment opportunities",
        "join our team",
        "join us",
        "opportunities",
        "work with us",
        "work for us",
        "hiring",
        "we're hiring",
        "open positions",
        "current openings",
    ]

    found_links = []

    for link in soup.find_all(
        "a",
        href=True,
    ):
        link_text = link.get_text(
            " ",
            strip=True,
        ).lower()

        href = link.get(
            "href",
            "",
        )

        href_lower = href.lower()

        if any(keyword in link_text or keyword in href_lower for keyword in keywords):
            full_url = urljoin(
                homepage,
                href,
            )

            if not is_valid_url(full_url):
                continue

            if full_url not in found_links:
                found_links.append(full_url)

    return {
        "company_name": company_name,
        "career_links": found_links,
    }


# =========================================================
# MAIN
# =========================================================


def main():
    print()
    print("================================")
    print("       PERSONAL JOB FINDER")
    print("================================")
    print()

    if not start_docker_desktop():
        return

    if not start_searxng():
        stop_docker_desktop()
        return

    database = connect_database()

    if database is None:
        stop_searxng()
        stop_docker_desktop()
        return

    print()
    print("Connected to job_finder database.")

    print()

    query = input("What should I search for? ")

    print()
    print(f'Searching for: "{query}"')

    results = search_searxng(query)

    print(f"Search results returned: " f"{len(results)}")

    if not results:
        print("No search results found.")

        database.close()
        stop_searxng()
        stop_docker_desktop()

        return

    visited_domains = set()

    websites_checked = 0
    career_sites_found = 0
    companies_saved = 0
    blocked_sites = 0
    blocked_countries = 0
    duplicates = 0
    no_career_page = 0

    for result in results:
        if not check_searxng_timer():
            break

        title = result.get(
            "title",
            "Unknown Company",
        )

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

        site_data = inspect_company_site(
            homepage=url,
            search_title=title,
            domain=domain,
        )

        company_name = site_data["company_name"]

        career_links = site_data["career_links"]

        print(f"Company: {company_name}")

        if not career_links:
            no_career_page += 1

            print("No obvious career " "page found.")

            continue

        career_sites_found += 1

        primary_career_url = career_links[0]

        print("Possible career pages:")

        for career_link in career_links:
            print(f"  {career_link}")

        career_response = safe_request(primary_career_url)

        if career_response is None:
            location_data = {
                "country": None,
                "state": None,
                "score": 0,
            }

        else:
            location_data = analyze_usa_location(career_response.text)

        print()
        print("Location information:")

        print("  Country: " f"{location_data['country'] or 'Unknown'}")

        print("  State: " f"{location_data['state'] or 'Unknown'}")

        print("  USA confidence: " f"{location_data['score']}")

        saved = save_company(
            connection=database,
            name=company_name,
            domain=domain,
            career_url=primary_career_url,
            source_url=url,
            country=location_data["country"],
            state=location_data["state"],
            usa_confidence=location_data["score"],
        )

        if saved:
            companies_saved += 1

            print("Saved to database.")

    database.close()

    print()
    print("=" * 60)
    print("SEARCH COMPLETE")
    print()

    print(f"Search results: " f"{len(results)}")

    print(f"Aggregators skipped: " f"{blocked_sites}")

    print(f"Non-US domains skipped: " f"{blocked_countries}")

    print(f"Duplicates skipped: " f"{duplicates}")

    print(f"Websites checked: " f"{websites_checked}")

    print(f"No career page: " f"{no_career_page}")

    print(f"Career sites found: " f"{career_sites_found}")

    print(f"Database saves: " f"{companies_saved}")

    stop_searxng()
    stop_docker_desktop()


if __name__ == "__main__":
    main()
