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

# =========================================================
# FILES
# =========================================================


# =========================================================
# LOAD SETTINGS
# =========================================================


# =========================================================
# PROGRAM SETTINGS
# =========================================================


_ats_http = None


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

web_search_started = False


# =========================================================
# COMPANY NAME DETECTION
# =========================================================


# Legal-entity codes some applicant systems put in front of a company name ("003 Humana Inc.").
_LEADING_CODE = re.compile(r"^\s*0\d{1,4}\s+(?=[A-Za-z])")  # zero-padded only: "84 Lumber" is a real name
# A posting on a recognised applicant-system board (Workday, Greenhouse, ...) is the employer's own listing.
OFFICIAL_BOARD_CREDIBILITY = 8


def tidy_company_name(name):
    """The company name without a leading entity code."""
    return _LEADING_CODE.sub("", str(name or "")).strip()


def verification_label(details, source_type, name, source_url):
    """Plain-language answer to "is this a real posting by the company?", shown next to the company website."""
    if source_type == EMPLOYER_SOURCE:
        return "Company's own careers site"
    board = details.get("ats_posting")
    if board:
        return f"Posted on the company's own {str(board.get('system', '')).title()} hiring board"
    site = details.get("employer_site")
    if site and site.get("domain") and get_domain(source_url or "") == site["domain"]:
        return "Posted on the company's own site"
    if site:
        return ("Listed on the company's website" if site.get("posting_found")
                else "Company website found; this job is not listed there")
    if source_url and not is_third_party(source_url, name):
        return "Posted on the company's own site"
    return "No company website found; not verified"


def on_company_site(url, name, details):
    """True when the page is on the company's own site: a name match, or a subdomain of its verified website."""
    host = get_domain(url or "")
    site = ((details or {}).get("employer_site") or {}).get("domain") or ""
    return bool(url and (not is_third_party(url, name or "") or (site and (host == site or host.endswith("." + site)))))


def on_official_board(url):
    return bool(identify_board(url))


def company_board_posting(name, title):
    """The job on the company's own hiring board (Ashby, Greenhouse, Lever, ...), or None."""
    global _ats_http
    if _ats_http is None:
        _ats_http = EmployerHttp(delay=0.2, timeout=15)
    try:
        return find_ats_posting(name, title, _ats_http)
    except Exception:
        return None


def clean_company_name(
    name,
    domain,
):
    if not name:
        return None

    cleaned = tidy_company_name(name)

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


_JOB_TITLE_WORDS = re.compile(
    r"\b(?:designer|developer|engineer|manager|specialist|coordinator|analyst|director|associate|intern|producer|"
    r"editor|writer|assistant|technician|consultant|architect|administrator|officer|representative|supervisor|"
    r"strategist|copywriter|artist|programmer)\b", re.I)


def company_from_title(title):
    """The company in a page title such as 'Senior Web Designer | Acme' (not the job title), or None."""
    title = re.sub(r"\s+", " ", str(title or "")).strip()
    parts = [part.strip() for part in re.split(r"\s[|–—:-]\s|\s::\s", title) if part.strip()]
    if len(parts) == 1:
        at = re.split(r"\s(?:at|@)\s", parts[0], maxsplit=1)
        if len(at) == 2 and _JOB_TITLE_WORDS.search(at[0]):
            return at[1].strip()
    for part in parts:
        if not _JOB_TITLE_WORDS.search(part):
            return part
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
            company_from_title(soup.title.string),
            domain,
        )

        if name:
            return name

    name = clean_company_name(
        company_from_title(search_title),
        domain,
    )

    if name:
        return name

    return domain


# =========================================================
# USA DETECTION
# =========================================================


# Abbreviations that are also credentials or words ("Jane Doe, MD"), so whole-page text needs more than a comma.
_AMBIGUOUS_ABBREVIATIONS = {"MD", "PA", "MA"}
_LOCATION_CUE = re.compile(r"(?:location|located|office|address|based in|campus|headquarter\w*)\b[^.]{0,45}$", re.I)


def has_location_cue(before):
    """True when the words just before a city read like a place ('Location: Towson', 'office in Towson')."""
    return bool(_LOCATION_CUE.search(str(before)[-90:]))


def find_state_from_text(text, strict=False):
    """The first U.S. state named in the text.

    strict is for whole-page text: an abbreviation such as MD then needs a ZIP code or wording
    like 'Location:' in front of the city, because 'Jane Doe, MD' is a doctor, not Maryland.
    """
    text_lower = text.lower()

    # Use the first state mentioned; longer names are tried first at each position
    # so "West Virginia" is not read as "Virginia".
    names = "|".join(re.escape(name) for name in sorted(US_STATES, key=len, reverse=True))
    match = re.search(rf"\b(?:{names})\b", text_lower)
    if match:
        return US_STATES[match.group(0)]

    # Strong abbreviation contexts such as "Gaithersburg, MD" or "MD 20877".
    abbreviations = "|".join(sorted(US_STATE_ABBREVIATIONS))
    abbreviation_pattern = (
        rf",\s*({abbreviations})\b"
        rf"|\b({abbreviations})\s+\d{{5}}(?:-\d{{4}})?\b"
    )

    for match in re.finditer(abbreviation_pattern, text):
        code = match.group(1) or match.group(2)
        if (strict and match.group(1) and code in _AMBIGUOUS_ABBREVIATIONS
                and not has_location_cue(text[:match.start()]) and not re.match(r"\s+\d{5}\b", text[match.end():])):
            continue
        return code

    return None


# Phrases that tie a remote job to where the applicant lives.
_RESIDENCY_TRIGGER = re.compile(
    r"(?:must|need to|required to|have to|should)\s+(?:currently\s+)?(?:reside|live|be\s+(?:located|based|a\s+resident))"
    r"|residents?\s+of|resident\s+in|(?:candidates|applicants)\s+(?:located\s+)?(?:in|from)\s+[A-Z]"
    r"|open\s+only\s+to|only\s+(?:open|available)\s+to|based\s+in\s+[A-Z][A-Za-z ]{2,20}\s+only"
    r"|residents?\s+only|out[- ]of[- ]state|in[- ]state\s+(?:candidates|residents|only)",
    re.I,
)
_OUT_OF_STATE = re.compile(r"out[- ]of[- ]state|in[- ]state\s+(?:candidates|residents|only)", re.I)
_STATE_NAMES = re.compile(r"\b(?:" + "|".join(re.escape(name) for name in sorted(US_STATES, key=len, reverse=True)) + r")\b", re.I)
# Abbreviations that are also ordinary words (IN, OR, ME...) only count right after a comma or bracket.
_WORD_LIKE_STATES = {"IN", "OR", "ME", "HI", "OK", "OH"}
_STATE_ABBREVIATION = re.compile(
    r"[,(]\s*(" + "|".join(sorted(US_STATE_ABBREVIATIONS)) + r")\b"
    r"|\b(" + "|".join(sorted(US_STATE_ABBREVIATIONS - _WORD_LIKE_STATES)) + r")\b")


def remote_state_restrictions(text, job_state=None, places=()):
    """States a remote job says its applicant must live in, or an empty set if it names none.

    "Cannot be out of state" with no state named means the job's own state.
    """
    text = str(text or "")[:8000]
    restricted = set()
    for trigger in _RESIDENCY_TRIGGER.finditer(text):
        window = text[trigger.start(): trigger.end() + 90]
        named = {US_STATES[match.group(0).casefold()] for match in _STATE_NAMES.finditer(window)}
        named |= {match.group(1) or match.group(2) for match in _STATE_ABBREVIATION.finditer(window)}
        if named:
            restricted |= named
        elif job_state and _OUT_OF_STATE.search(window):
            restricted.add(job_state)
    # Location strings such as "Work At Home-Florida" and lists such as "open in the following states: ..."
    return restricted | restriction_states(places, text)


def selected_state_codes(state_text, cities, statewide_states):
    """Every state the user selected, from the state box, statewide picks and city names."""
    codes = set(statewide_states)
    parts = re.split(r"[,/;]", state_text or "")
    for item in cities or []:
        if isinstance(item, dict):
            parts.append(str(item.get("city", "")).split(",")[-1])
    for part in parts:
        part = part.strip()
        if part.casefold() in US_STATES:
            codes.add(US_STATES[part.casefold()])
        elif part.upper() in US_STATE_ABBREVIATIONS:
            codes.add(part.upper())
    return codes


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


_PAGE_NOISE_ELEMENTS = ["select", "option", "datalist", "footer", "nav", "aside"]
_PAGE_NOISE_NAMES = re.compile(r"related|similar|recommended|sidebar|cookie|consent|breadcrumb|newsletter|footer|more[-_ ]jobs|other[-_ ]jobs", re.I)
# Wording that names a state without saying where the job is.
_STATE_BOILERPLATE = re.compile(
    r"\b[Aa]n?\s+[A-Z][a-z]+(?:\s[A-Z][a-z]+)?\s+(?i:corporation|company|limited liability company|llc|nonprofit|non-profit|partnership)"
    r"|(?i:incorporated|organized|registered|chartered|headquartered|domiciled)\s+(?i:in|under the laws of)\s+(?i:the\s+state\s+of\s+)?[A-Z][a-z]+(?:\s[A-Z][a-z]+)?"
    r"|\b[A-Z][a-z]+(?:\s[A-Z][a-z]+)?\s+(?:Consumer\s+Privacy|Privacy|Fair\s+Chance|Fair\s+Employment|Pay\s+Transparency|Equal\s+Pay|Paid\s+Sick|Human\s+Rights|Civil\s+Rights|Workers'?\s+Compensation)\b"
    r"|\b(?:Washington|New\s+York)\s+(?:Post|Times|Examiner|Mutual|Life|Yankees|Mets|Giants|Jets|Knicks|Rangers|Nationals|Capitals|Wizards|Commanders|Magazine)\b"
    r"|\bIndiana\s+Jones\b|\bVirginia\s+Woolf\b|\bKansas\s+City\b"
    # A list of the company's offices says nothing about where this job is.
    r"|\b(?i:offices)\b\s*(?i:in|include|located in|:)?\s*[^.]{0,120}")


def page_body_text(soup):
    """Visible text of the main content: no dropdowns, menus, footers, sidebars or 'related jobs' lists."""
    doomed = list(soup(_PAGE_NOISE_ELEMENTS))
    doomed += soup.find_all(class_=_PAGE_NOISE_NAMES) + soup.find_all(id=_PAGE_NOISE_NAMES)
    for tag in doomed:
        if not getattr(tag, "decomposed", False):
            tag.decompose()
    return soup.get_text(" ", strip=True)


def analyze_usa_location(html, extra_text="", source_label="page", page_url=""):
    soup = BeautifulSoup(html, "html.parser")
    # The listing's own location is trusted; the rest of the page is searched with the noise taken out.
    location_text = str(extra_text or "")
    body = _STATE_BOILERPLATE.sub(" ", page_body_text(soup))
    combined = f"{location_text} {body}".strip()
    lower = combined.lower()
    evidence = []
    country = None
    state = None
    score = 0

    # Count a country mention once, even when the page says US, USA and United States.
    # The listing's own location often ends in ", US" (capitals only, so the word "us" never counts).
    listing_says_us = bool(re.search(r"(?<![A-Za-z])U\.?S\.?A?(?![A-Za-z])", location_text))
    if ("united states" in lower or re.search(r"\busa\b|\bu\.s\.a?\.?\b", lower) or listing_says_us):
        score += 4
        country = "United States"
        evidence.append(f"{source_label}: U.S. country mention (+4)")

    state = find_state_from_text(location_text) or find_state_from_text(body, strict=True)
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

    # A ZIP code counts only next to a state or the word ZIP; a salary like $90000 is not one.
    if re.search(r"\b(?:" + "|".join(sorted(US_STATE_ABBREVIATIONS)) + r")\s+\d{5}(?:-\d{4})?\b|(?i:\bzip(?: code)?)\s*:?\s*\d{5}\b", combined):
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
    """Classify explicit job arrangements without guessing from site boilerplate.

    'Not remote' is Onsite, 'remote sensing' and 'remote work stipend' are not Remote, and an
    in-person interview does not make a job Onsite (see job_listings.arrangement_types).
    """
    def classify(text):
        found = arrangement_types(text)
        return next(iter(found)) if len(found) == 1 else None

    if job_title:
        title_types = arrangement_types(job_title)
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


_last_search_time = 0.0
# Engines SearXNG said it could not use on its most recent search, e.g. ["duckduckgo: CAPTCHA"].
last_search_health = {"unresponsive": []}


def search_blocked_message(empty_queries):
    engines = ", ".join(last_search_health["unresponsive"][:4]) or "no engine gave a reason"
    return (f"Search engines returned nothing for {empty_queries} queries in a row ({engines}). "
            "They are probably rate-limiting Job Finder; try again in about an hour.")


def search_searxng(query, page=1):
    with timed("Search engine queries (including polite pauses)"):
        return _search_searxng(query, page)


def _search_searxng(query, page=1):
    global _last_search_time
    if not docker.check_searxng_timer():
        return []

    # Spacing requests out keeps engines like DuckDuckGo from answering with a CAPTCHA.
    wait = shared.QUERY_DELAY - (time.monotonic() - _last_search_time)
    if wait > 0:
        time.sleep(wait)

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

        last_search_health["unresponsive"] = [
            f"{item[0]}: {item[1]}" for item in data.get("unresponsive_engines", []) if len(item) >= 2]

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

    finally:
        _last_search_time = time.monotonic()


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

# Titles of "find a designer" / "best agencies" pages. Always a directory.
DIRECTORY_TITLE_PATTERNS = [re.compile(pattern, re.I) for pattern in (
    r"\bfind an? (?:\w+ ){0,2}(?:agency|agencies|designers?|developers?|companies|company|providers?|professionals?|freelancers?|experts?)\b",
    r"\bbest (?:\w+ ){0,3}(?:agencies|designers|developers|companies)\b",
    r"\btop (?:\d+ )?(?:\w+ ){0,3}(?:agencies|designers|developers|companies)\b",
    r"\bcompare providers\b",
    r"\bget quotes\b",
)]
# "Reviews" only marks a directory when the title is not a job title ("Product Reviews Content Designer").
DIRECTORY_WEAK_TITLE = re.compile(r"\breviews?\b", re.I)


def is_directory_or_marketplace_result(domain, title="", html=""):
    if any(domain == item or domain.endswith("." + item) for item in DIRECTORY_MARKETPLACE_DOMAINS):
        return True

    title = title or ""
    if any(pattern.search(title) for pattern in DIRECTORY_TITLE_PATTERNS):
        return True
    if DIRECTORY_WEAK_TITLE.search(title) and not _JOB_TITLE_WORDS.search(title):
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

    if not shared.update_existing_mode and not docker.check_searxng_timer():
        return []

    try:
        time.sleep(shared.REQUEST_DELAY)
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


def inspect_company_site(homepage, search_title, domain, deep=True):
    """Read a page and, when deep, the site's about/contact pages, sitemap and robots.txt for location and career links.

    A direct job posting is not read deeply: its own page already says where the job is, and the extra pages
    (often ten or more requests to one site) only add the company's head-office address.
    """
    response = fetching.safe_request(homepage)

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

    support_links = discover_support_links(soup, homepage) if deep else []
    sitemap_links = discover_sitemap_links(homepage) if deep else []
    robots_links = discover_robots_links(homepage) if deep else []

    for support_url in support_links + sitemap_links + robots_links:
        support_response = fetching.safe_request(support_url)
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


# =========================================================
# TITLE RELEVANCE AND EMPLOYER SITES
# =========================================================


def expand_job_titles(titles):
    """The typed titles plus up to two related O*NET titles for each."""
    expanded = list(titles)
    seen = {title.casefold() for title in titles}
    for title in titles:
        role = title.casefold().split()[-1]
        added = 0
        for suggestion in related_title_suggestions(title, limit=12):
            if role in suggestion.casefold().split() and suggestion.casefold() not in seen:
                expanded.append(suggestion)
                seen.add(suggestion.casefold())
                added += 1
                if added == 2:
                    break
    return expanded


def load_user_titles(database):
    """Job titles from the saved profile plus the most recent search, or [] if unknown."""
    titles = []
    cursor = None
    try:
        cursor = database.cursor()
        cursor.execute("SELECT job_title FROM user_profile_job_titles WHERE profile_id = 1")
        titles.extend(row[0] for row in cursor.fetchall())
        cursor.execute("SELECT job_title FROM search_history ORDER BY searched_at DESC LIMIT 1")
        latest = cursor.fetchone()
        if latest and latest[0]:
            titles.extend(part.strip() for part in latest[0].split(","))
    except Error:
        pass
    finally:
        if cursor is not None:
            cursor.close()
    unique = {}
    for title in titles:
        if title and title.strip():
            unique.setdefault(title.strip().casefold(), title.strip())
    return list(unique.values())


def load_city_targets(database):
    """(city radius targets, state text) from the most recent search, so Refresh can fill in missing distances."""
    cursor = None
    try:
        cursor = database.cursor()
        cursor.execute("SELECT state, cities_json FROM search_history ORDER BY searched_at DESC LIMIT 1")
        latest = cursor.fetchone()
    except Error:
        return [], ""
    finally:
        if cursor is not None:
            cursor.close()
    if not latest:
        return [], ""
    try:
        cities = json.loads(latest[1] or "[]")
    except ValueError:
        cities = []
    return prepare_city_targets(database, latest[0] or "", cities), latest[0] or ""


def load_selected_states(database):
    """State codes from the most recent search (state box plus the states of its cities), or an empty set."""
    cursor = None
    try:
        cursor = database.cursor()
        cursor.execute("SELECT state, cities_json FROM search_history ORDER BY searched_at DESC LIMIT 1")
        latest = cursor.fetchone()
        if not latest:
            return set()
        try:
            cities = json.loads(latest[1] or "[]")
        except ValueError:
            cities = []
        return selected_state_codes(latest[0] or "", cities, set())
    except Error:
        return set()
    finally:
        if cursor is not None:
            cursor.close()


def stale_remote_limit(company, details, selected_states, http):
    """States a saved remote job is limited to when none of them is selected, else None.

    Older rows were saved before "Work At Home-<State>" locations were read; a Workday posting can be rechecked.
    """
    if (company.get("is_kept") or company.get("work_arrangement") != "Remote" or "remote_limited_to" in details
            or not selected_states or company.get("source_type") in FEED_NAMES):
        return None
    config = identify_board(company.get("source_url") or "")
    if not config or config.get("system") != "workday":
        return None
    try:
        limits = Employer(dict(config, name=company.get("name") or "Employer")).remote_limits(company["source_url"], http)
    except (requests.RequestException, ValueError, KeyError, TypeError):
        return None
    return limits if limits and not (limits & selected_states) else None


def is_internship_row(company, details):
    """An unsaved row that is an internship or co-op (saved rows are never touched)."""
    return bool(not company.get("is_kept") and is_internship(company.get("career_job_title"), details.get("schedule")))


def is_irrelevant_lead(company, details, wanted):
    """An unsaved lead from before titles were checked whose title matches none of the user's."""
    title = (company.get("career_job_title") or "").strip()
    return bool(wanted and title and not company.get("is_kept") and not details.get("matched_title")
                and company.get("source_type") not in FEED_NAMES
                and not matching_job_title(title, wanted))


def is_wrong_location_lead(company, details):
    """An unsaved lead whose listing location is outside the United States (when the search is U.S.-only)."""
    return bool(USA_ONLY and not company.get("is_kept") and company.get("source_type") not in FEED_NAMES
                and details.get("location") and excludes_us(details.get("location"), ""))


def reject_irrelevant_row(database, company_id, reason="wrong_role"):
    """Move the row to Rejected Listings for review; Restore puts it back as it was."""
    cursor = database.cursor()
    try:
        cursor.execute(
            """
            UPDATE companies
            SET pre_reject_kept = is_kept, pre_reject_status = application_status,
                is_rejected = 1, is_kept = 0, application_status = 'Rejected',
                rejected_at = CURRENT_TIMESTAMP, rejection_reason = %s, rejected_by = 'system'
            WHERE id = %s AND is_kept = 0 AND is_rejected = 0
            """,
            (reason, company_id),
        )
        database.commit()
        return cursor.rowcount == 1
    finally:
        cursor.close()


def is_excluded_employer_host(host):
    """Sites that can never be an employer's own website."""
    return (is_blocked_domain(host) or has_blocked_country_domain(host)
            or any(host == blocked or host.endswith("." + blocked)
                   for blocked in SOCIAL_DOMAINS | ATS_DOMAINS | DIRECTORY_MARKETPLACE_DOMAINS))


def apply_link_closed(page_url, page_html):
    """Follow a job-board listing's Apply button; return why it is closed, or None if it looks open."""
    def get(url):
        wait_for_host(url)
        try:
            return requests.get(url, headers=HEADERS, timeout=TIMEOUT, allow_redirects=False)
        except requests.RequestException:
            return None
    return listing_closed(page_url, page_html, get)


def fetch_text(url, max_bytes=6_000_000):
    """Any text file (sitemap.xml, robots.txt) as an object with .url and .text, or None; read politely and size-capped."""
    if not is_valid_url(url):
        return None
    try:
        wait_for_host(url)
        response = requests.get(url, headers=HEADERS, timeout=TIMEOUT, stream=True)
        if not response.ok:
            return None
        content = b""
        for chunk in response.iter_content(chunk_size=65536):
            content += chunk
            if len(content) > max_bytes:
                break
        response.close()
        return SimpleNamespace(url=response.url, text=content.decode("utf-8", errors="replace"))
    except requests.RequestException:
        return None


def find_employer_site(name, job_title, job_url, page_html, titles, location_hint="", allow_search=True, notes=None):
    return resolve_employer_site(
        name, job_title, job_url, page_html, titles, fetch=fetching.safe_request,
        search=search_searxng if allow_search else None, score_page=score_career_page,
        is_excluded=is_excluded_employer_host, location_hint=location_hint,
        allow_search=allow_search, notes=notes, fetch_raw=fetch_text)


# =========================================================
# UPDATE EXISTING RESULTS
# =========================================================


SEARCH_SCOPE_FILE = paths.SEARCH_SCOPE_FILE
RESTORABLE_REASONS = ("wrong_role", "wrong_location")


def current_search_scope(database):
    """What the profile asks for now (titles, cities with radius, state) in a comparable form, or None if unreadable."""
    titles, cities, state, problem = load_profile_filters(database)
    if problem:
        return None
    return {
        "titles": sorted({title.strip().casefold() for title in titles if title.strip()}),
        "cities": sorted(f"{str(item.get('city') or '').strip().casefold()}|{item.get('radius') or ''}" for item in cities),
        "state": str(state or "").strip().casefold(),
    }


def location_matches_scope(row, city_targets, statewide, selected_states):
    """Would this saved listing's place pass the location filter with the profile as it is now?"""
    code = US_STATES.get(str(row.get("state") or "").strip().casefold())
    if code and code in statewide:
        return True
    try:
        point = (float(row["latitude"]), float(row["longitude"]))
    except (KeyError, TypeError, ValueError):
        point = None
    if city_targets:
        return bool(point) and any(miles_between(point, (target["lat"], target["lon"])) <= target["radius"]
                                   for target in city_targets)
    return bool(code) and code in selected_states


def restore_rejected_row(database, company_id):
    """Put a listing the system rejected back in the results, as the Restore button does."""
    with database.cursor() as cursor:
        cursor.execute(
            """UPDATE companies
               SET is_rejected = 0, is_kept = COALESCE(pre_reject_kept, 0),
                   application_status = COALESCE(pre_reject_status, 'None'),
                   rejected_at = NULL, rejection_reason = NULL, rejected_by = NULL,
                   pre_reject_kept = NULL, pre_reject_status = NULL
               WHERE id = %s AND is_rejected = 1 AND rejected_by = 'system'""", (company_id,))
    database.commit()


def recheck_system_rejections(database, scope_file=None):
    """When the profile's titles or places changed since the last check, look again at the listings Job Finder itself
    rejected for the wrong title or the wrong place, and restore those that now fit. A listing the person rejected is
    never touched (rejected_by is 'user', or empty for older rows). Returns how many were restored."""
    scope_file = Path(scope_file or SEARCH_SCOPE_FILE)
    scope = current_search_scope(database)
    if scope is None:
        return 0
    try:
        earlier = json.loads(scope_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        earlier = None
    try:
        scope_file.write_text(json.dumps(scope), encoding="utf-8")
    except OSError:
        pass
    if earlier is None or earlier == scope:
        return 0
    titles, cities, state, _ = load_profile_filters(database)
    wanted = expand_job_titles(titles) if titles else []
    wanted += [title for title in related_family_titles(titles) if title.casefold() not in {w.casefold() for w in wanted}]
    statewide = {US_STATES[str(item["city"]).strip().casefold()] for item in cities
                 if str(item["city"]).strip().casefold() in US_STATES}
    selected_states = selected_state_codes(state, cities, statewide)
    city_targets = prepare_city_targets(database, state, cities)
    with database.cursor(dictionary=True) as cursor:
        placeholders = ", ".join(["%s"] * len(RESTORABLE_REASONS))
        cursor.execute(
            f"""SELECT id, career_job_title, state, latitude, longitude, rejection_reason FROM companies
                WHERE is_rejected = 1 AND rejected_by = 'system' AND rejection_reason IN ({placeholders})""",
            RESTORABLE_REASONS)
        rows = cursor.fetchall()
    restored = 0
    for row in rows:
        if row["rejection_reason"] == "wrong_role":
            fits = bool(wanted) and matching_job_title(row.get("career_job_title") or "", wanted)
        else:
            fits = location_matches_scope(row, city_targets, statewide, selected_states)
        if fits:
            restore_rejected_row(database, row["id"])
            restored += 1
    return restored


def recheck_system_rejections_quietly(database):
    """Runs at the start of a search or Refresh; never stops it."""
    try:
        restored = recheck_system_rejections(database)
    except Exception:
        return
    if restored:
        print(f"Search changed: {restored} listing(s) rejected earlier now fit and were restored.", flush=True)


def close_expired_listings(database, today=None):
    """Mark Closed the open listings whose own closing date (kept when the page was captured) has passed.
    Returns how many were closed."""
    today = today or date.today()
    with database.cursor() as cursor:
        cursor.execute("""SELECT id, listing_details FROM companies
                          WHERE job_open_status <> 'Closed' AND listing_details LIKE %s""", ('%"closes"%',))
        rows = cursor.fetchall()
    expired = []
    for company_id, raw in rows:
        try:
            closes = date.fromisoformat(str((json.loads(raw or "{}") or {}).get("closes") or "")[:10])
        except (TypeError, ValueError, AttributeError):
            continue
        if closes < today:
            expired.append(company_id)
    if expired:
        now = datetime.now(timezone.utc)
        with database.cursor() as cursor:
            for company_id in expired:
                cursor.execute("""UPDATE companies SET job_open_status = 'Closed', last_checked = %s, result_updated_at = %s
                                  WHERE id = %s""", (now, now, company_id))
        database.commit()
    return len(expired)


def close_expired_listings_quietly(database):
    try:
        closed = close_expired_listings(database)
    except Exception:
        return
    if closed:
        print(f"Closed: {closed} listing(s) passed their closing date.", flush=True)


def refresh_job_fit_quietly(database):
    """Listings saved earlier pick up skills the current skills list recognizes, so Job Fit uses them. Never stops a run."""
    try:
        changed = refresh_listing_skills(database)
    except Exception:
        return
    if changed:
        print(f"Job Fit: {changed} saved listing(s) now list newly recognized skills.", flush=True)


def update_existing_results(company_ids=None):
    """Recheck existing rows without starting Docker or the search engine."""
    shared.update_existing_mode = True
    print()
    print("================================")
    print("     UPDATE EXISTING RESULTS")
    print("================================")
    print()

    database = storage.connect_database()
    if database is None:
        return

    if not storage.ensure_database_schema(database):
        database.close()
        return
    refresh_job_fit_quietly(database)
    recheck_system_rejections_quietly(database)
    close_expired_listings_quietly(database)
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
                listing_details,
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

    wanted_titles = expand_job_titles(load_user_titles(database))
    selected_now = load_selected_states(database)
    city_targets_now, search_state_text = load_city_targets(database)
    clear_employer_cache()
    shared._debug_run = DebugRun(shared.SEARCH_DEBUG_FILE, "update", JOB_FINDER_VERSION)
    shared._debug_run.set_inputs(rows_checked=len(companies), update_ids=company_ids,
                          titles_used_for_relevance=wanted_titles)

    updated = 0
    failed = 0
    rejected_count = 0
    employers = load_employers() if any(row.get("source_type") == EMPLOYER_SOURCE for row in companies) else []
    employer_http = EmployerHttp()
    feed_listings = {}  # provider name -> {listing URL: listing}, or None when its feed could not be read
    for feed in FEEDS:
        if any(row.get("source_type") == feed.name for row in companies):
            try:
                feed_listings[feed.name] = feed.index(feed.fetch())
            except (requests.RequestException, ValueError, TypeError) as error:
                feed_listings[feed.name] = None
                print(f"{feed.name} refresh unavailable: {error}")

    for index, company in enumerate(companies, start=1):
        if (index - 1) % 20 == 0:
            prefetch_for_update([url for row in companies[index - 1:index + 19]
                                 if row.get("source_type") not in FEED_NAMES and row.get("source_type") != EMPLOYER_SOURCE
                                 and row.get("source_type") not in CAPTURE_SOURCES
                                 and row.get("source_type") not in JOB_SITE_NAMES
                                 for url in (row.get("source_url"), row.get("career_url"))])
        company_id = company["id"]
        if company.get("source_type") in CAPTURE_SOURCES:
            # LinkedIn and the other job sites answer with a sign-in page, which would read as a closed job, and
            # direct visits could get noticed. The Web Job Scraper extension reports these jobs' status instead.
            print()
            print(f"Updating {index}/{len(companies)}: {company.get('name') or company.get('domain')}")
            print(f"Not rechecked: {company.get('source_type')} jobs are kept current by the Web Job Scraper extension.")
            continue
        career_url = company.get("career_url")
        source_url = company.get("source_url")
        search_title = company.get("career_job_title") or company.get("name") or ""
        try:
            details = json.loads(company.get("listing_details") or "{}")
        except (TypeError, ValueError):
            details = {}
        work_arrangement = detect_work_arrangement(search_title) or company.get("work_arrangement")
        skills = None

        print()
        print("=" * 60)
        print(f"Updating {index}/{len(companies)}: {company.get('name') or company.get('domain')}")
        remote_limit = None
        if not (is_irrelevant_lead(company, details, wanted_titles) or is_wrong_location_lead(company, details)):
            remote_limit = stale_remote_limit(company, details, selected_now, employer_http)
        internship = is_internship_row(company, details)
        reject_reason = ("wrong_role" if internship or is_irrelevant_lead(company, details, wanted_titles)
                         else "wrong_location" if is_wrong_location_lead(company, details) or remote_limit else None)
        if reject_reason:
            try:
                rejected = reject_irrelevant_row(database, company_id, reject_reason)
            except Error as error:
                database.rollback()
                failed += 1
                print(f"Could not reject this row: {error}")
                continue
            if rejected:
                updated += 1
                rejected_count += 1
                why = ("is an internship" if internship
                       else "matches none of your job titles" if reject_reason == "wrong_role"
                       else f"is remote but only open to residents of {', '.join(sorted(remote_limit))}" if remote_limit
                       else f"is located outside the United States ({details.get('location')})")
                print(f"Rejected: '{search_title}' {why}. It is in Rejected Listings for review.")
                shared._debug_run.lead(action="rejected", id=company_id, title=search_title,
                                company=company.get("name"), url=source_url or career_url,
                                reason=reject_reason, detail=why)
                continue
        tidy_name = tidy_company_name(company.get("name"))
        if tidy_name and tidy_name != company.get("name"):
            try:
                with database.cursor() as rename_cursor:
                    rename_cursor.execute("UPDATE companies SET name = %s WHERE id = %s", (tidy_name, company_id))
                database.commit()
                print(f"Company name tidied: {company.get('name')} -> {tidy_name}")
                company["name"] = tidy_name
            except Error as error:
                database.rollback()
                print(f"Could not tidy the company name: {error}")
        site_class = job_site_for(company.get("source_type"))
        if site_class:
            # A job-site opening is open while the site still lists it (its page is an app that shows nothing to
            # a plain download, so the page itself can't be checked).
            status = site_class().status(source_url)
            try:
                with database.cursor() as status_cursor:
                    status_cursor.execute(
                        """UPDATE companies SET job_open_status = %s, last_checked = %s,
                           result_updated_at = CASE WHEN job_open_status <> %s THEN %s ELSE result_updated_at END
                           WHERE id = %s""",
                        (status, datetime.now(timezone.utc), status, datetime.now(timezone.utc), company_id))
                database.commit()
                updated += 1
                print(f"{site_class.name} says: {status}.")
                print("Existing row updated in place.")
            except Error as error:
                database.rollback()
                failed += 1
                print(f"Could not update {company.get('name')}: {error}")
            continue
        if company.get("source_type") == EMPLOYER_SOURCE:
            employer = employer_for_url(source_url or "", employers)
            try:
                status = employer.status(source_url, employer_http) if employer else "Unknown"
            except (requests.RequestException, ValueError, KeyError, TypeError) as error:
                status = "Unknown"
                print(f"Could not check {company.get('name')}: {error}")
            try:
                with database.cursor() as status_cursor:
                    status_cursor.execute(
                        """UPDATE companies SET job_open_status = %s, last_checked = %s,
                           result_updated_at = CASE WHEN job_open_status <> %s THEN %s ELSE result_updated_at END
                           WHERE id = %s""",
                        (status, datetime.now(timezone.utc), status, datetime.now(timezone.utc), company_id))
                database.commit()
                updated += 1
                print(f"Employer careers site says: {status}.")
            except Error as error:
                database.rollback()
                failed += 1
                print(f"Could not update {company.get('name')}: {error}")
            continue
        if company.get("source_type") in FEED_NAMES:
            feed_name = company.get("source_type")
            remote_feed = feed_listings.get(feed_name)
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
                    print(f"{feed_name} listing still appears in the recent feed.")
                except Error as error:
                    database.rollback()
                    failed += 1
                    print(f"Could not refresh {feed_name} listing: {error}")
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
                    print(f"Could not update {feed_name} listing status: {error}")
            else:
                failed += 1
                print(f"{feed_name} feed unavailable; status left unchanged.")
            continue

        career_credibility = 0
        job_open_status = company.get("job_open_status") or "Open"
        location_data = {
            "country": None,
            "state": None,
            "score": 0,
            "evidence": [],
        }
        employer = None
        employer_notes = []
        apply_closed = None
        ats_found = False
        repaired_domain = None
        landing_html = ""

        if source_url and is_valid_url(source_url):
            direct_posting = (company.get("source_type") == EMPLOYER_SOURCE or bool(
                {"JobPosting data", "job detail page", "direct listing link"} & set(details.get("evidence") or [])))
            # A row with missing location data still gets the deep read, so refresh keeps filling it in.
            direct_posting = direct_posting and bool(company.get("country")) and bool(company.get("usa_credibility"))
            source_data = inspect_company_site(
                homepage=source_url,
                search_title=search_title,
                domain=company.get("domain") or get_domain(source_url),
                deep=not direct_posting,
            )
            location_data = merge_location_data(location_data, source_data["location"])
            work_arrangement = work_arrangement or detect_work_arrangement("", source_data.get("landing_html", ""))
            landing_html = source_data.get("landing_html", "")
            # A job-board repost can outlive the real opening; check where its Apply button leads.
            if is_third_party(source_url, company.get("name") or ""):
                apply_closed = apply_link_closed(source_url, source_data.get("landing_html", ""))
            # A listing found through a job board should link to the employer's own careers page.
            if not details.get("employer_site") and is_third_party(source_url, company.get("name") or ""):
                employer = find_employer_site(
                    company.get("name") or "", search_title, source_url, source_data.get("landing_html", ""),
                    wanted_titles or [search_title], location_hint=str(details.get("location") or ""),
                    allow_search=False, notes=employer_notes)
                if employer:
                    career_url = employer["posting_url"] or source_url
                    details["employer_site"] = {"domain": employer["domain"], "careers_url": employer["careers_url"],
                                                "method": employer["method"],
                                                "posting_found": bool(employer["posting_url"])}
                    details["original_source"] = source_url
                    details["evidence"] = list(details.get("evidence") or []) + employer["evidence"]
                    print(f"Employer site found: {employer['domain']} ({employer['method']}) -> {career_url}")
                else:
                    print("Employer site not found: " + "; ".join(employer_notes or ["no candidates"]))
        if (not details.get("ats_posting") and source_url and is_third_party(source_url, company.get("name") or "")
                and not company.get("is_kept")):
            board_posting = company_board_posting(company.get("name") or "", search_title)
            if board_posting:
                career_url = board_posting["url"]
                details["ats_posting"] = {"system": board_posting["system"], "url": board_posting["url"]}
                details.setdefault("original_source", source_url)
                details["evidence"] = list(details.get("evidence") or []) + [
                    f"posting found on the company's own {board_posting['system'].title()} board"]
                ats_found = True
                print(f"Company's own posting found: {career_url}")
        saved_site = details.get("employer_site") or {}
        listing_host = get_domain(source_url or "")
        if (saved_site.get("domain") and listing_host != saved_site["domain"]
                and listing_host.endswith("." + saved_site["domain"])):
            # The listing is on a subdomain of the company's website (corcoran.gwu.edu under gwu.edu): show that site.
            saved_site["domain"] = repaired_domain = listing_host
            saved_site["posting_found"] = True
            print(f"Company website is {listing_host} (the listing's own site)")
        employer_row = bool(details.get("employer_site"))
        # Rows saved earlier linked View to the employer's careers page; point it back at the job itself.
        saved_careers = (details.get("employer_site") or {}).get("careers_url")
        repaired_view = ats_found
        if saved_careers and source_url and career_url == saved_careers and source_url != saved_careers:
            career_url = source_url
            repaired_view = True
            print(f"View link now goes to the job posting itself: {source_url}")

        if career_url and is_valid_url(career_url):
            shared._last_failure_status = None
            response = fetching.safe_request(career_url)
            if response is not None:
                # The employer's careers page is not the job description, so keep the skills already saved.
                if not employer_row or canonical_url(career_url) == canonical_url(source_url or ""):
                    skills = listing_skills(response.text)
                current_postings = extract_jobs(career_url, response.text, [search_title])
                matching_posting = next((opening for opening in current_postings
                                         if canonical_url(opening["url"]) == canonical_url(career_url)), None)
                # A page's structured URL can differ from the saved one; accept its only
                # posting as long as the page itself was not redirected elsewhere.
                if (matching_posting is None and len(current_postings) == 1
                        and canonical_url(response.url) == canonical_url(career_url)):
                    matching_posting = current_postings[0]
                work_arrangement = (matching_posting or {}).get("type") or work_arrangement or detect_work_arrangement("", response.text)
                career_data = score_career_page(career_url, response.text)
                career_credibility = career_data["score"]
                active_posting = bool(details and matching_posting)
                if active_posting:
                    career_credibility = max(CAREER_CREDIBILITY_THRESHOLD, career_credibility)
                    if on_official_board(career_url):
                        career_credibility = max(OFFICIAL_BOARD_CREDIBILITY, career_credibility)
                if details.get("ats_posting"):
                    career_credibility = max(OFFICIAL_BOARD_CREDIBILITY, career_credibility)
                # An employer page that does not list the job says nothing about the original listing.
                if details and not active_posting and not employer_row:
                    job_open_status = "Unknown"
                elif active_posting or career_credibility >= CAREER_CREDIBILITY_THRESHOLD:
                    job_open_status = "Open"
                elif career_credibility <= 2 and not employer_row:
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
            elif shared._last_failure_status in (404, 410):
                job_open_status = "Closed"
                print(f"Career page returned HTTP {shared._last_failure_status}; marking job Closed.")
            else:
                job_open_status = "Unknown"
                print("Career page could not be loaded right now; marking job Unknown.")
        else:
            job_open_status = "Closed"
            print("No usable career URL; marking job Closed.")

        if apply_closed:
            job_open_status = "Closed"
            print(f"Job is closed: {apply_closed['reason']} ({apply_closed['url'][:90]})")
            shared._debug_run.note(f"row {company_id} ({company.get('name')}) marked Closed: {apply_closed['reason']} "
                            f"[{apply_closed['apply_link']}]")

        if location_data["score"] == 0 and not location_data["evidence"]:
            location_data["country"] = company.get("country")
            location_data["state"] = company.get("state")
            location_data["score"] = company.get("usa_credibility") or 0

        # A row saved before city radii (or with its address at the bottom of the page) gets its distance filled in.
        found_place = None
        if (city_targets_now and landing_html and company.get("distance_miles") is None and not company.get("city")
                and work_arrangement != "Remote"):
            _, place_city, place_lat, place_lon, place_miles = distance_to_city_targets(
                database, landing_html, str(details.get("location") or search_title), search_state_text, city_targets_now,
                allow_footer=on_company_site(source_url, company.get("name"), details))
            if place_lat is not None:
                found_place = (place_city, place_lat, place_lon, place_miles)
                print(f"Distance filled in: {place_city}, {place_miles} miles from the nearest selected city")
        new_label = verification_label(details, company.get("source_type"), company.get("name"), source_url)
        details_dirty = bool(employer) or ats_found or bool(repaired_domain) or details.get("verification") != new_label
        details["verification"] = new_label
        update_cursor = None
        try:
            changed = any((
                company.get("work_arrangement") != work_arrangement,
                company.get("career_credibility") != career_credibility,
                (company.get("job_open_status") or "Open") != job_open_status,
                company.get("country") != location_data.get("country"),
                company.get("state") != location_data.get("state"),
                (company.get("usa_credibility") or 0) != location_data.get("score", 0),
                employer is not None,
                repaired_view,
                found_place is not None,
                bool(apply_closed),
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
                    domain = COALESCE(%s, domain),
                    career_url = COALESCE(%s, career_url),
                    listing_details = COALESCE(%s, listing_details),
                    city = COALESCE(%s, city),
                    latitude = COALESCE(%s, latitude),
                    longitude = COALESCE(%s, longitude),
                    distance_miles = COALESCE(%s, distance_miles),
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
                    employer["domain"] if employer else repaired_domain,
                    career_url if (employer or repaired_view) else None,
                    json.dumps(details) if details_dirty else None,
                    found_place[0] if found_place else None,
                    found_place[1] if found_place else None,
                    found_place[2] if found_place else None,
                    found_place[3] if found_place else None,
                    checked_at,
                    changed,
                    checked_at,
                    company_id,
                ),
            )
            database.commit()
            updated += 1
            print("Existing row updated in place.")
            if employer:
                shared._debug_run.lead(action="employer site added", id=company_id, title=search_title,
                                company=company.get("name"), original_source=source_url,
                                employer_site=details["employer_site"], view_link=career_url)
            elif employer_notes:
                shared._debug_run.note(f"row {company_id} ({company.get('name')}): " + "; ".join(employer_notes))
        except Error as error:
            database.rollback()
            failed += 1
            print("Could not update this existing row.")
            print(error)
        finally:
            if update_cursor is not None:
                update_cursor.close()

    try:
        expired = tidy_closed_jobs(database)
        if expired:
            print(f"Deleted {expired} job(s) closed for more than {CLOSED_KEEP_DAYS} days (saved jobs are kept).")
    except Error as error:
        print(f"Could not tidy closed jobs: {error}")
    database.close()

    print()
    print("=" * 60)
    print("UPDATE COMPLETE")
    print(f"Rows updated: {updated}")
    print(f"Rows rejected (wrong role or outside the U.S.): {rejected_count}")
    print(f"Rows failed: {failed}")
    shared._debug_run.finish(f"update complete: {updated} updated, {rejected_count} rejected, {failed} failed")
    shared._debug_run = None


# =========================================================
# WEB JOB SCRAPER IMPORT
# =========================================================


def load_profile_filters(database):
    """(job titles, cities, state, error) from the saved profile only, so imports don't change with each search."""
    titles, cities, state, problem = [], [], "", None
    cursor = None
    try:
        cursor = database.cursor()
        cursor.execute("SELECT job_title FROM user_profile_job_titles WHERE profile_id = 1 ORDER BY id")
        titles = [row[0].strip() for row in cursor.fetchall() if row[0] and row[0].strip()]
        cursor.execute("SELECT city, radius_miles FROM user_profile_cities WHERE profile_id = 1 ORDER BY id")
        cities = [{"city": row[0], "radius": row[1]} for row in cursor.fetchall() if row[0]]
        cursor.execute("SELECT state FROM user_profile WHERE id = 1")
        row = cursor.fetchone()
        state = (row[0] if row else "") or ""
    except Error as error:
        problem = str(error)
    finally:
        if cursor is not None:
            cursor.close()
    return titles, cities, state, problem


def site_location_in_us(location):
    """The job site's own location field names a U.S. state or the United States ("Austin, TX", "United States (Remote)")."""
    text = str(location or "")
    return bool(find_state_from_text(text) or re.search(r"united states|(?<![A-Za-z])USA?(?![A-Za-z])", text, re.I))


def _row_details(row):
    try:
        details = json.loads(row.get("listing_details") or "{}")
    except (TypeError, ValueError):
        details = {}
    return details if isinstance(details, dict) else {}


class CaptureImport:
    """One --import-captures run: the profile filters and the rows already saved, shared by every job."""

    def __init__(self, database):
        self.database = database
        titles, cities, self.state, self.profile_error = load_profile_filters(database)
        self.wanted = expand_job_titles(titles) if titles else []
        self.wanted += [title for title in related_family_titles(titles)
                        if title.casefold() not in {wanted.casefold() for wanted in self.wanted}]
        self.statewide = {US_STATES[str(item["city"]).strip().casefold()] for item in cities
                          if str(item["city"]).strip().casefold() in US_STATES}
        self.selected_states = selected_state_codes(self.state, cities, self.statewide)
        self.city_targets = prepare_city_targets(database, self.state, cities)
        self.rejected = storage.rejected_posting_urls(database)
        self.by_url = {}
        self.by_key = {}
        with database.cursor(dictionary=True) as cursor:
            cursor.execute("""SELECT id, name, career_job_title, work_arrangement, city, state, source_url, source_type,
                                     listing_details, is_kept
                              FROM companies WHERE is_rejected = 0""")
            for row in cursor.fetchall():
                self._remember(row)

    def _remember(self, row):
        details = _row_details(row)
        if row.get("source_url"):
            self.by_url[canonical_url(row["source_url"])] = row
        place = details.get("location") or ", ".join(part for part in (row.get("city"), row.get("state")) if part)
        key = match_key(row.get("name"), row.get("career_job_title"), place, row.get("work_arrangement"))
        if key:
            self.by_key.setdefault(key, row)

    def _saved_row(self, url):
        with self.database.cursor(dictionary=True) as cursor:
            cursor.execute("""SELECT id, name, career_job_title, work_arrangement, city, state, source_url, source_type,
                                     listing_details, is_kept FROM companies WHERE source_url = %s LIMIT 1""", (url,))
            return cursor.fetchone()

    def _set_details(self, row_id, details):
        with self.database.cursor() as cursor:
            cursor.execute("UPDATE companies SET listing_details = %s WHERE id = %s", (json.dumps(details), row_id))
        self.database.commit()

    def _mark_closed(self, row_id):
        now = datetime.now(timezone.utc)
        with self.database.cursor() as cursor:
            cursor.execute("""UPDATE companies SET job_open_status = 'Closed', last_checked = %s,
                              result_updated_at = CASE WHEN job_open_status <> 'Closed' THEN %s ELSE result_updated_at END
                              WHERE id = %s""", (now, now, row_id))
        self.database.commit()

    def _mark_applied(self, row_id):
        """Saved, with status Applied. A status further along (Interview) is kept; a rejected row comes back."""
        with self.database.cursor() as cursor:
            cursor.execute("""UPDATE companies
                              SET is_kept = 1, is_rejected = 0, rejection_reason = NULL, rejected_at = NULL,
                                  application_status = CASE WHEN application_status IN ('None', 'Saved', 'Rejected')
                                                            THEN 'Applied' ELSE application_status END
                              WHERE id = %s""", (row_id,))
        self.database.commit()

    def company_site(self, company, title, url, location, details, site_domain):
        """The company's own website for a captured job: (domain, View link). Looked up once per job, the way
        web-search leads from other job boards are: a domain guessed from the company name and checked to be that
        company, its careers page, this job on it, or the job on the company's hiring board (Greenhouse, Lever, ...).
        Only the company's sites are visited, never LinkedIn. Updates details in place."""
        if company and not details.get("employer_checked"):
            details["employer_checked"] = True
            print(f"Looking for {company}'s own website...", flush=True)
            notes = []
            employer = find_employer_site(company, title, url, "", self.wanted or [title], location_hint=location,
                                          allow_search=False, notes=notes)
            if employer:
                details["employer_site"] = {
                    "domain": employer["domain"], "careers_url": employer["careers_url"], "method": employer["method"],
                    "posting_found": bool(employer["posting_url"]), "posting_url": employer["posting_url"],
                    "evidence": list(employer["evidence"]),
                }
                print(f"Company website: {employer['domain']} ({employer['method']})"
                      + (f" — this job is there: {employer['posting_url']}" if employer["posting_url"] else ""))
            else:
                print("Company website not found: " + "; ".join(notes or ["no candidates"]))
            if not (employer and employer["posting_url"]):
                board = company_board_posting(company, title)
                if board:
                    details["ats_posting"] = {"system": board["system"], "url": board["url"]}
                    print(f"This job is on the company's own {board['system'].title()} board: {board['url']}")
        site = details.get("employer_site") or {}
        board = details.get("ats_posting") or {}
        # Re-imports rebuild the evidence list, so the company-site findings are added back from what was stored.
        extra = list(site.get("evidence") or [])
        if board:
            extra.append(f"posting found on the company's own {str(board.get('system', '')).title()} board")
        details["evidence"] = list(dict.fromkeys(list(details.get("evidence") or []) + extra))
        if site.get("domain"):
            details["original_source"] = url
        return site.get("domain") or site_domain, site.get("posting_url") or board.get("url") or url

    def assess(self, job, arrangement):
        """assess_opening's result for a captured job, or a plain pass for one without a location."""
        location = str(job.get("location") or "").strip()
        if not location:
            return {"skip": None, "arrangement": arrangement, "location": {"score": 0}, "detail_score": CAREER_CREDIBILITY_THRESHOLD,
                    "country": None, "state_name": None, "remote_limited_to": set(), "city": None, "lat": None, "lon": None,
                    "miles": None}
        opening = {"title": job["title"], "company": job.get("company") or "", "description": job.get("description") or "",
                   "type": arrangement, "location": location, "locations": [location], "remote_states": [],
                   "schedule": "", "evidence": []}
        outcome = assess_opening(self.database, opening, capture_page_html(job), job["url"], self.state,
                                 self.selected_states, self.statewide, self.city_targets)
        # A card has no page text to prove the job is in the U.S.; the site's own location field is trusted instead.
        if outcome["skip"] == "US eligibility unverified" and site_location_in_us(location):
            outcome["skip"] = None
            outcome["country"] = "United States"
            outcome["location"]["score"] = max(outcome["location"]["score"], shared.USA_CREDIBILITY_THRESHOLD)
        return outcome

    def import_job(self, site, job):
        """Filter and save one captured job. Returns (result, reason) where result is added, updated, linked,
        rejected or skipped.

        A job you already applied to skips the filters: it is always saved, with status Applied."""
        source_type, domain = CAPTURE_SITES[site]
        url, title = job["url"], job["title"]
        company = tidy_company_name(job.get("company")) or None
        applied = bool(job.get("applied"))
        row = self.by_url.get(url)
        if not applied:
            if not row and url in self.rejected:
                return "skipped", "Previously rejected"
            if company and company.casefold() in BLOCKED_COMPANIES:
                return "skipped", "Blocked company"
            if not self.wanted or not matching_job_title(title, self.wanted):
                return "skipped", "Title matches none of your profile's job titles"
            if is_internship(title):
                return "skipped", "Internship"
        closed = bool(job.get("closed"))
        location = str(job.get("location") or "").strip()
        arrangement = job.get("work_arrangement") or detect_work_arrangement(title, location) or None
        outcome = self.assess(job, arrangement)
        if outcome["skip"] and not applied:
            details = _row_details(row) if row else {}
            # A job imported earlier as "Location unknown" is filtered again now that its location is known.
            if row and details.get("location_unknown") and not row.get("is_kept"):
                if reject_irrelevant_row(self.database, row["id"], "wrong_location"):
                    self.by_url.pop(url, None)
                    return "rejected", outcome["skip"]
            return "skipped", outcome["skip"]
        arrangement = outcome["arrangement"] or arrangement

        if not row:
            key = match_key(company, title, location, arrangement)
            same_job = self.by_key.get(key) if key else None
            if same_job:
                details = _row_details(same_job)
                if add_also_on(details, site, url):
                    self._set_details(same_job["id"], details)
                    same_job["listing_details"] = json.dumps(details)
                if applied:
                    self._mark_applied(same_job["id"])
                return "linked", (f"same job as #{same_job['id']} ({same_job.get('source_type') or 'saved'})"
                                  + ("; marked Applied" if applied else ""))
            if closed and not applied:
                return "skipped", "No longer accepting applications"

        matched_title = next((wanted for wanted in self.wanted if matching_job_title(title, [wanted])), None)
        new_details = {
            "captured_by": CAPTURE_MARK, "site": source_type, "external_id": str(job.get("job_id") or ""),
            "capture_level": job.get("level") or "seen", "page_kind": job.get("page_kind") or "other",
            "location": location, "salary": job.get("salary") or "", "posted": job.get("posted") or "",
            "description": str(job.get("description") or "")[:20000], "closes": str(job.get("closes") or "")[:10], "matched_title": matched_title,
            "location_unknown": not location, "remote_limited_to": sorted(outcome["remote_limited_to"]),
            "evidence": [f"Seen on {source_type}", "you applied" if applied else "matching title"],
        }
        details = merge_details(_row_details(row) if row else {}, new_details)
        domain, career_url = self.company_site(company, title, url, location, details, domain)
        details["verification"] = verification_label(details, source_type, company, url)
        inserted = storage.save_company(
            self.database, company, title, outcome["detail_score"], domain, career_url, url, outcome["country"],
            str(outcome["state_name"] or "")[:100] or None, outcome["location"]["score"],
            city=outcome["city"], latitude=outcome["lat"], longitude=outcome["lon"], distance_miles=outcome["miles"],
            work_arrangement=arrangement, skills=listing_skills(details.get("description") or ""),
            source_type=source_type, listing_details=details)
        saved = self._saved_row(url)
        if saved:
            self._remember(saved)
            if closed:
                self._mark_closed(saved["id"])
            if applied:
                self._mark_applied(saved["id"])
        notes = [note for note, on in (("you applied: saved as Applied", applied), ("closed on the site", closed)) if on]
        return ("added" if inserted else "updated"), "; ".join(notes)


def import_captures():
    """--import-captures: filter and save the jobs in web-job-scraper\\searches that changed since the last import."""
    # Like Refresh, the import runs without the search engine: page downloads (the company-website check) must not
    # wait for SearXNG, which is not running.
    shared.update_existing_mode = True
    print()
    print("================================")
    print("     IMPORT CAPTURED JOBS")
    print("================================")
    print()
    _, searches_dir = capture_dirs(settings, BASE_DIR)
    for path in unreadable_files(searches_dir):
        print(f"[{IMPORT_ERRORS['bad_file']}] Skipped a capture file that could not be read: {path}")
    paths = files_to_import(searches_dir)
    work = []
    for path in paths:
        site, jobs = read_jobs(path)
        work.extend((path, site, job) for job in jobs)
    print(f"Capture files to import: {len(paths)} ({len(work)} jobs) from {searches_dir}")
    if not paths:
        print("Stop reason: Nothing new to import.")
        return

    def stop(key, message):
        print(f"Stop reason: [{IMPORT_ERRORS[key]}] Import stopped: {message}")

    database = storage.connect_database()
    if database is None:
        stop("database", "the database could not be reached. Is MySQL (XAMPP) running?")
        return
    if not storage.ensure_database_schema(database):
        database.close()
        stop("schema", "the database could not be updated.")
        return
    counts = {"added": 0, "updated": 0, "linked": 0, "rejected": 0, "skipped": 0}
    failed = 0
    stopped = False
    try:
        run = CaptureImport(database)
        if run.profile_error:
            stop("profile", f"your profile could not be read ({run.profile_error}).")
            return
        if not run.wanted:
            stop("no_titles", "add job titles to your profile first.")
            return
        print("Matching job titles: " + ", ".join(run.wanted))
        for number, (path, site, job) in enumerate(work, start=1):
            if shared.stop_requested():
                stopped = True
                break
            print(f"Importing {number}/{len(work)}: {job['title'][:70]} ({CAPTURE_SITES[site][0]})", flush=True)
            try:
                result, reason = run.import_job(site, job)
            except Error as error:
                database.rollback()
                failed += 1
                result, reason = "skipped", f"[{IMPORT_ERRORS['save']}] could not be saved: {error}"
            counts[result] += 1
            if result == "skipped":
                print(f"Skipped ({reason}): {job['title'][:70]} {job['url']}")
            else:
                print(f"{result.title()}: {job['title'][:70]}" + (f" — {reason}" if reason else ""))
        if not stopped and not failed:
            try:
                mark_imported(searches_dir, paths)
                removed = remove_old_folders(searches_dir)
                if removed:
                    print(f"Removed {len(removed)} capture folder(s) older than 30 days: {', '.join(removed)}")
            except OSError as error:
                print(f"[{IMPORT_ERRORS['files']}] Could not record the imported files or remove old folders: {error}")
        try:
            expired = tidy_closed_jobs(database)
            if expired:
                print(f"Deleted {expired} job(s) closed for more than {CLOSED_KEEP_DAYS} days (saved jobs are kept).")
        except Error as error:
            print(f"[{IMPORT_ERRORS['tidy']}] Could not tidy closed jobs: {error}")
    finally:
        database.close()
    print()
    print(f"Import summary: checked {sum(counts.values())}, added {counts['added']}, updated {counts['updated']}, "
          f"linked {counts['linked']}, rejected {counts['rejected']}, skipped {counts['skipped']}")
    linked = f", {counts['linked']} matched jobs already in your list" if counts["linked"] else ""
    # Files with a failed job stay unmarked, so the next import tries them again.
    errors = (f" [{IMPORT_ERRORS['save']}] {failed} job(s) could not be saved and will be tried again next time."
              if failed else "")
    print(f"Stop reason: {'Import stopped early' if stopped else 'Import finished'}: {counts['added']} added, "
          f"{counts['updated']} updated{linked}, {counts['skipped'] + counts['rejected'] - failed} filtered out.{errors}")


# =========================================================
# CITY / DISTANCE FILTERING
# =========================================================

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"


def _read_app_version():
    """APP_VERSION lives in dashboard.py, where start.ps1 and update.ps1 also read it."""
    try:
        source = (BASE_DIR / "dashboard.py").read_text(encoding="utf-8")
    except OSError:
        return "unknown"
    match = re.search(r'^APP_VERSION\s*=\s*["\']([0-9]+\.[0-9]+\.[0-9]+)["\']', source, re.M)
    return match.group(1) if match else "unknown"


JOB_FINDER_VERSION = _read_app_version()
_last_nominatim_request = 0.0
_failed_geocode_queries = set()


def _normalize_geocode_query(value):
    # The suffix retires cached answers saved before places were ranked by kind (see pick_place).
    return re.sub(r"\s+", " ", (value or "").strip()).lower()[:240] + " [v2]"


# How much a kind of place looks like the town or city a person means (lower is better).
_PLACE_KIND_RANK = {"city": 0, "town": 0, "administrative": 0, "municipality": 1, "borough": 1, "suburb": 2,
                    "census_designated_place": 2, "village": 3, "hamlet": 4, "statistical": 5, "neighbourhood": 5}


def pick_place(results):
    """The best match among several: a real city or town beats a village or statistical area of the same name.

    Nominatim ranked a tiny "Bel Air" in Allegany County above Bel Air in Harford County, 100 miles away.
    """
    return min(results, key=lambda item: (_PLACE_KIND_RANK.get(item.get("type"), 4), -float(item.get("importance") or 0)))


def _place_name(value):
    value = re.sub(r"\bst\.? ", "saint ", str(value or "").strip().casefold())
    value = re.sub(r"\bmt\.? ", "mount ", value)
    return re.sub(r"\bft\.? ", "fort ", value)


def place_matches(query, display_name):
    """True when the map result is the place that was asked for, not a road or a similar name elsewhere."""
    city = _place_name((query or "").split(",")[0])
    first = _place_name((display_name or "").split(",")[0])
    if not city or not first:
        return False
    suffixes = ("city", "town", "village", "township", "borough", "county", "cdp")
    return (first == city or first in {f"{city} {suffix}" for suffix in suffixes}
            or first in {f"city of {city}", f"town of {city}", f"village of {city}"})


def geocode_location(database, query, require_place_match=False):
    """Geocode once with public Nominatim, then reuse the MySQL cache.

    require_place_match rejects results that are not the named place, such as a road
    called "New London Road" when the query was "London".
    """
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
            if require_place_match and not place_matches(query, cached.get("display_name")):
                return None
            return {"lat": float(cached["latitude"]), "lon": float(cached["longitude"]), "display_name": cached.get("display_name")}
    finally:
        cursor.close()

    elapsed = time.monotonic() - _last_nominatim_request
    if elapsed < 1.05:
        time.sleep(1.05 - elapsed)

    try:
        response = requests.get(
            NOMINATIM_URL,
            params={"q": query, "format": "jsonv2", "limit": 5, "countrycodes": "us"},
            headers={"User-Agent": f"JobFinder/{JOB_FINDER_VERSION} (https://github.com/jltkerig/job-finder-dashboard)"},
            timeout=15,
        )
        _last_nominatim_request = time.monotonic()
        response.raise_for_status()
        results = response.json()
        if not results:
            _failed_geocode_queries.add(key)
            return None
        result = pick_place(results)
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
        if require_place_match and not place_matches(query, display_name):
            print(f"Geocode for {query} rejected: it resolved to {display_name[:60]}, not that place.")
            return None
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


def extract_job_city(html, fallback_text="", allow_footer=False):
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

    # No structured address: look for "City, ST" in the listing's location first, then the page body.
    # A ZIP code or "Location:" wording before the city beats a name like "Contact Jane Doe, MD".
    # The whole page, footer included, is kept for the last-resort address lookup below (the next call strips it).
    full_text = soup.get_text(" ", strip=True) if allow_footer else ""
    body = page_body_text(soup)
    # "Washington, D.C. 20006" is the District's usual spelling; read it as "DC".
    body = re.sub(r"\bD\.\s?C\.?(?=\s*\d{5}\b|\s|$)", "DC", body)
    leading_noise = {"contact", "email", "call", "address", "location", "located", "office", "offices", "visit", "our",
                     "at", "in", "based", "dr", "mr", "ms", "mrs", "team", "meet", "join", "apply", "posted",
                     "nw", "ne", "sw", "se", "north", "south", "east", "west"}
    candidates = []
    for source, text in ((0, str(fallback_text or "")), (1, body)):
        for match in re.finditer(r"\b([A-Z][A-Za-z'.]+(?:[ -][A-Z][A-Za-z'.]+){0,2}),\s*([A-Z]{2})\b(\s+\d{5})?", text):
            if match.group(2) not in US_STATE_ABBREVIATIONS and match.group(2) != "DC":
                continue
            words = match.group(1).split(" ")
            while words and words[0].casefold().rstrip(".") in leading_noise:
                words.pop(0)
            if not words:
                continue
            rank = (source, 0 if match.group(3) else 1, 0 if has_location_cue(text[:match.start()]) else 1, match.start())
            candidates.append((rank, " ".join(words), match.group(2)))
    if candidates:
        _, city, region = min(candidates)
        return city, region
    # On the employer's own site, the street address in the footer is where the office is: use the last
    # "City, ST 12345" on the page. (On a job board the footer is the board's address, so this is not used.)
    if allow_footer:
        full_text = re.sub(r"\bD\.\s?C\.?(?=\s*\d{5}\b)", "DC", full_text)
        addresses = [match for match in re.finditer(
            r"\b([A-Z][A-Za-z'.]+(?:[ -][A-Z][A-Za-z'.]+){0,2}),\s*([A-Z]{2})\s+\d{5}\b", full_text)
            if match.group(2) in US_STATE_ABBREVIATIONS or match.group(2) == "DC"]
        if addresses:
            last = addresses[-1]
            words = last.group(1).split(" ")
            while words and words[0].casefold().rstrip(".") in leading_noise:
                words.pop(0)
            if words:
                return " ".join(words), last.group(2)
    return None, None


_ZIP_CODE = re.compile(r"\d{5}(?:-\d{4})?")
_REGION_WORDS = re.compile(r"\b(?:greater|metropolitan|metro|area|region|metroplex)\b", re.I)
# Regions people use in place of a city, mapped to the city that anchors them.
_REGION_ALIASES = {"dmv": "Washington, DC", "dc metro": "Washington, DC", "washington dc": "Washington, DC",
                   "national capital": "Washington, DC", "tri-state": None, "delmarva": None}


def clean_region_name(place):
    """"Greater Baltimore Area" -> "Baltimore"; "DMV" -> "Washington, DC". Other places come back unchanged."""
    text = re.sub(r"\s+", " ", str(place or "")).strip()
    key = _REGION_WORDS.sub(" ", text).strip(" ,-").casefold()
    key = re.sub(r"\s+", " ", key)
    if key in _REGION_ALIASES:
        return _REGION_ALIASES[key] or text
    if _REGION_WORDS.search(text) and key:
        return re.sub(r"\s+", " ", _REGION_WORDS.sub(" ", text)).strip(" ,-")
    return text


def geocode_queries(city, state_text):
    """What to ask the geocoder for a typed location: a ZIP code, "City, ST", or a county/city with each searched state."""
    city = str(city or "").strip()
    if _ZIP_CODE.fullmatch(city):
        return [f"{city[:5]}, United States"]
    if "," in city:
        return [city]
    states = [part.strip() for part in re.split(r"[,/;]", state_text or "") if part.strip()]
    return [f"{city}, {state_name}" for state_name in states] or [city]


def prepare_city_targets(database, state, cities):
    targets = []
    allowed = {5, 10, 15, 20, 30, 50}
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
        point = None
        for query in geocode_queries(city, state):
            point = geocode_location(database, query)
            if point:
                break
        if point:
            targets.append({"city": city, "radius": radius, **point})
            print(f"City radius: {city} — {radius} miles")
    return targets


def distance_to_city_targets(database, html, fallback_text, state, targets, allow_footer=False):
    if not targets:
        return True, None, None, None, None
    city, detected_state = extract_job_city(html, fallback_text, allow_footer=allow_footer)
    if not city:
        return False, None, None, None, None
    city = clean_region_name(city)
    if "," in city:
        # A region alias can carry its own state ("Washington, DC").
        city, _, alias_state = city.partition(",")
        detected_state = alias_state.strip() or detected_state
    query_state = detected_state or state
    point = geocode_location(database, f"{city}, {query_state}", require_place_match=True)
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


def assess_opening(database, opening, page_html, job_url, search_state, selected_states, statewide_states, city_targets):
    """The U.S., location and remote checks every source shares (web pages, employer career sites).

    Returns a dict. outcome["skip"] is the reason the job is out, or None when it passes; the rest
    (arrangement, location analysis, city, distance, ...) is what gets saved with it.
    """
    text = opening["description"] or BeautifulSoup(page_html, "html.parser").get_text(" ", strip=True)
    arrangement = opening["type"] or detect_work_arrangement(opening["title"], page_html)
    outcome = {"skip": None, "arrangement": arrangement, "location": None, "detail_score": 0, "country": None,
               "state_name": None, "remote_limited_to": set(), "city": None, "lat": None, "lon": None, "miles": None}
    if is_internship(opening.get("title"), opening.get("schedule")):
        outcome["skip"] = "Internship"
        return outcome
    if excludes_us(opening["location"], text):
        outcome["skip"] = "Posting restricts applicants outside the US"
        return outcome
    location = analyze_usa_location(page_html, extra_text=opening["location"], source_label=f"job posting {job_url}", page_url=job_url)
    # An individual opening was already confirmed, so it meets the career-credibility threshold.
    detail_score = max(CAREER_CREDIBILITY_THRESHOLD, score_career_page(job_url, page_html)["score"])
    # A confirmed opening on the employer's own applicant-system board (or read from an employer careers API)
    # is strong evidence even when the page is script-rendered and scores little on its text.
    if on_official_board(job_url) or any("careers site (" in str(line) for line in opening.get("evidence") or ()):
        detail_score = max(detail_score, OFFICIAL_BOARD_CREDIBILITY)
    # The listing's own location beats anything found elsewhere on the page.
    state_name = (find_state_from_text(opening["location"] or "") or location["state"] or opening["location"] or None)
    code = US_STATES.get(str(state_name or "").casefold(), str(state_name or "").upper())
    outcome.update(location=location, detail_score=detail_score, country=location["country"], state_name=state_name)
    # Remote jobs pass any location filter unless the listing says the applicant must live in particular
    # states. A statewide match passes even when city radii are also selected.
    within_radius, skip_reason = True, "Outside selected location"
    if arrangement == "Remote":
        limited_to = remote_state_restrictions(text, code if code in US_STATE_ABBREVIATIONS else None,
                                               opening.get("locations") or [opening.get("location")])
        limited_to |= set(opening.get("remote_states") or ())
        outcome["remote_limited_to"] = limited_to
        if limited_to and selected_states and not (limited_to & selected_states):
            within_radius = False
            skip_reason = "Remote job limited to residents of " + ", ".join(sorted(limited_to))
    elif statewide_states and code in statewide_states:
        pass
    elif city_targets:
        within_radius, outcome["city"], outcome["lat"], outcome["lon"], outcome["miles"] = distance_to_city_targets(
            database, page_html, opening["location"] or opening["title"], search_state, city_targets,
            allow_footer=not is_third_party(job_url, opening.get("company") or ""))
    elif statewide_states:
        within_radius = False
    if not within_radius:
        outcome["skip"] = skip_reason
    elif USA_ONLY and location["score"] < shared.USA_CREDIBILITY_THRESHOLD:
        outcome["skip"] = "US eligibility unverified"
    return outcome


def _run_search(job_title=None, state=None, cities_json=None, max_new=None):
    global web_search_started
    _page_cache.clear()
    _skip_decisions.clear()
    _skip_decisions.update(latest_decisions(SEARCH_SKIPS_FILE))
    if max_new is not None:
        shared.MAX_SEARCH_RESULTS = max_new
    print()
    print("================================")
    print("       PERSONAL JOB FINDER")
    print("================================")
    print()

    database = storage.connect_database()

    if database is None:
        return

    if not storage.ensure_database_schema(database):
        database.close()
        return
    refresh_job_fit_quietly(database)
    recheck_system_rejections_quietly(database)
    close_expired_listings_quietly(database)

    rejected_urls = storage.rejected_posting_urls(database)

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

    corrected_titles = []
    for index, typed_title in enumerate(job_titles):
        fixed = spelling_fix(typed_title)
        if fixed.casefold() != typed_title.casefold():
            print(f'Spelling corrected: "{typed_title}" -> "{fixed}"')
            corrected_titles.append((typed_title, fixed))
            job_titles[index] = fixed
    selected_titles = list(dict.fromkeys(job_titles))
    job_titles = expand_job_titles(selected_titles)
    if len(job_titles) > len(selected_titles):
        print("Related O*NET search titles: " + ", ".join(job_titles[len(selected_titles):]))
    # Web queries use the typed and O*NET titles; the related families below are only used to recognise openings.
    query_titles = list(job_titles)
    related_extras = [title for title in related_family_titles(selected_titles) if title.casefold() not in {t.casefold() for t in job_titles}]
    job_titles = job_titles + related_extras
    if related_extras:
        print("Also matching closely related titles: " + ", ".join(related_extras))

    try:
        cities = json.loads(cities_json or "[]")
        if not isinstance(cities, list):
            cities = []
    except json.JSONDecodeError:
        cities = []
    statewide_states = {US_STATES[str(item.get("city", "")).strip().casefold()]
                        for item in cities if isinstance(item, dict)
                        and str(item.get("city", "")).strip().casefold() in US_STATES}
    selected_states = selected_state_codes(state, cities, statewide_states)
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
    if shared._debug_run is not None:
        shared._debug_run.set_inputs(
            job_title_typed=job_title, typed_titles=selected_titles,
            related_onet_titles=query_titles[len(selected_titles):], related_family_titles=related_extras,
            spelling_corrections=[{"typed": a, "corrected": b} for a, b in corrected_titles], state=state, cities_requested=cities,
            cities_geocoded=[{"city": target.get("city"), "radius": target.get("radius"),
                              "lat": target.get("lat"), "lon": target.get("lon")} for target in city_targets],
            statewide_states=sorted(statewide_states), max_new_results=shared.MAX_SEARCH_RESULTS,
            settings={"usa_only": USA_ONLY, "max_search_pages": shared.MAX_SEARCH_PAGES,
                      "request_delay_seconds": shared.REQUEST_DELAY, "website_timeout_seconds": TIMEOUT,
                      "searxng_timeout_minutes": shared.settings.get("searxng_timeout_minutes")},
            blocked_domain_count=len(BLOCKED_DOMAINS), blocked_company_count=len(BLOCKED_COMPANIES))

    print()
    print(f'Job titles: "{", ".join(job_titles)}"')
    print(f'State: "{state}"')
    print(f'Searching {len(job_titles)} job title(s).')

    fetched_pages = set()
    seen_openings = set()
    seen_result_urls = set()
    results_checked = 0
    candidate_number = 0
    pages_checked = 0


    websites_checked = 0
    companies_saved = 0
    passed_count = 0
    blocked_sites = 0
    blocked_countries = 0
    duplicates = 0
    existing_companies = 0
    no_career_page = 0

    # Public remote-job feeds. Each provider's own page stays the View link and the provider is named,
    # as their terms ask; employer URLs are not invented.
    feed_saved_total = 0
    feed_cap = max(1, shared.MAX_SEARCH_RESULTS // 3)
    for feed in FEEDS:
        if feed_saved_total >= feed_cap or companies_saved >= shared.MAX_SEARCH_RESULTS:
            break
        # Turned on by default. The blocked-domains list only affects web-search results, so these
        # aggregator sites can stay blocked there; switch one off here: "remote_feeds": {"Remotive": false}.
        if not shared.settings.get("remote_feeds", {}).get(feed.name, True):
            print(f"{feed.name} is turned off in settings.json.")
            _board_health.note(feed.name, "Remote feed", "off")
            continue
        print(f"Checking {feed.name} for matching remote listings...")
        try:
            feed_jobs = list(feed.matching(feed.fetch(), job_titles, USA_ONLY))
        except (requests.RequestException, ValueError, TypeError) as error:
            print(f"{feed.name} unavailable; continuing: {error}")
            _board_health.failed(feed.name, "Remote feed", error)
            continue
        print(f"{feed.name} matches: {len(feed_jobs)}")
        feed_saved = 0
        for job in feed_jobs:
            if (companies_saved >= shared.MAX_SEARCH_RESULTS or feed_saved_total >= feed_cap
                    or feed_saved >= max(1, shared.MAX_SEARCH_RESULTS // 3)):
                break
            candidate_number += 1
            results_checked += 1
            print(f"Checking result {candidate_number}: {job['title'][:75]} ({feed.name})")
            if not job["name"] or job["name"].casefold() in BLOCKED_COMPANIES:
                debug_skip("Blocked company", job["url"], job["title"])
                continue
            if canonical_url(job["url"]) in rejected_urls:
                record_skip("Previously rejected", job["url"], job["title"])
                continue
            if is_internship(job["title"]):
                record_skip("Internship", job["url"], job["title"])
                continue
            limited_to = remote_state_restrictions(job["text"])
            if limited_to and selected_states and not (limited_to & selected_states):
                record_skip("Remote job limited to residents of " + ", ".join(sorted(limited_to)), job["url"], job["title"])
                continue
            matched_title = next((wanted for wanted in job_titles if matching_job_title(job["title"], [wanted])), job_titles[0])
            inserted = storage.save_company(
                database, job["name"], job["title"], 6, feed.domain,
                job["url"], job["url"], "United States" if job["usa_score"] == 6 else None,
                job["location"][:100] or None, job["usa_score"], work_arrangement="Remote",
                skills=listing_skills(job["html"]), source_type=feed.name,
                listing_details={"location": job["location"], "posted": job.get("posted"),
                                 "evidence": [f"{feed.name} feed", "matching title"], "matched_title": matched_title,
                                 "verification": "From a remote-job feed; the company site was not checked"},
            )
            passed_count += 1
            print(f"Passed validation: {passed_count}")
            debug_lead(action="saved" if inserted else "already saved (updated)", source=f"{feed.name} feed",
                       title=job["title"], company=job["name"], url=job["url"], location=job["location"],
                       matched_title=matched_title, usa_score=job["usa_score"])
            if inserted:
                companies_saved += 1
                feed_saved += 1
                feed_saved_total += 1
                print(f"Saved viable company ({companies_saved}/{shared.MAX_SEARCH_RESULTS}).")
            else:
                existing_companies += 1
        _board_health.note(feed.name, "Remote feed", "ok" if feed_jobs else "no matches", len(feed_jobs), feed_saved)

    # Big employers' own career sites (watched_employers.json): Workday and Oracle searches. They go through
    # the same U.S., location and remote checks as web results.
    employer_saved_total = 0
    # Employer boards share up to half of a search's jobs, two per board, so several get a turn.
    employer_cap = max(1, shared.MAX_SEARCH_RESULTS // 2)
    employer_http = EmployerHttp()
    def search_employer(employer, per_employer_cap, total_cap):
        """Search one employer's board and save passing openings; returns how many were saved."""
        nonlocal candidate_number, results_checked, companies_saved, existing_companies, passed_count, employer_saved_total
        if employer_saved_total >= total_cap or companies_saved >= shared.MAX_SEARCH_RESULTS or shared.stop_requested():
            return 0
        print(f"Checking {employer.name} careers...")
        try:
            employer_openings = employer.find_openings(job_titles, employer_http)
        except (requests.RequestException, ValueError, KeyError, TypeError) as error:
            print(f"{employer.name} careers unavailable; continuing: {error}")
            _board_health.failed(employer.name, "Discovered board" if employer.discovered else "Employer board", error)
            return 0
        print(f"{employer.name} title matches: {len(employer_openings)}")
        if employer.discovered:
            record_board_result(employer.config, len(employer_openings))
        employer_saved = 0
        for opening in employer_openings:
            if (companies_saved >= shared.MAX_SEARCH_RESULTS or employer_saved_total >= total_cap
                    or employer_saved >= per_employer_cap):
                break
            job_url = canonical_url(opening["url"])
            candidate_number += 1
            results_checked += 1
            print(f"Checking result {candidate_number}: {opening['title'][:75]} ({employer.name})")
            if not job_url or job_url in seen_openings:
                continue
            if job_url in rejected_urls:
                record_skip("Previously rejected", job_url, opening["title"])
                continue
            seen_openings.add(job_url)
            if employer.name.casefold() in BLOCKED_COMPANIES:
                debug_skip("Blocked company", job_url, opening["title"])
                continue
            if USA_ONLY and opening["country"] and "united states" not in opening["country"].casefold():
                record_skip("Posting restricts applicants outside the US", job_url, opening["title"])
                continue
            # A posting can list several places; it passes if any one of them fits.
            outcome = None
            for place in opening["locations"]:
                outcome = assess_opening(database, dict(opening, location=place), opening["html"], job_url, state,
                                         selected_states, statewide_states, city_targets)
                if not outcome["skip"]:
                    opening = dict(opening, location=place)
                    break
            if outcome["skip"]:
                record_skip(outcome["skip"], job_url, opening["title"])
                continue
            matched_title = next((wanted for wanted in job_titles + employer.extra_titles
                                  if matching_job_title(opening["title"], [wanted])), job_titles[0])
            details = {"schedule": opening["schedule"], "salary": opening["salary"], "posted": opening["posted"],
                       "evidence": opening["evidence"], "location": opening["location"], "matched_title": matched_title,
                       "remote_limited_to": sorted(outcome["remote_limited_to"]),
                       "verification": "Company's own careers site",
                       "source": employer.adapter.base if hasattr(employer.adapter, "base") else employer.domain}
            inserted = storage.save_company(database, employer.name, opening["title"], outcome["detail_score"], employer.domain,
                                    job_url, job_url, outcome["country"],
                                    str(outcome["state_name"] or "")[:100] or None, outcome["location"]["score"],
                                    city=outcome["city"], latitude=outcome["lat"], longitude=outcome["lon"],
                                    distance_miles=outcome["miles"], work_arrangement=outcome["arrangement"],
                                    skills=listing_skills(opening["html"]), source_type=EMPLOYER_SOURCE,
                                    listing_details=details)
            record_decision(SEARCH_SKIPS_FILE, _skip_decisions, "Passed", job_url, opening["title"])
            passed_count += 1
            print(f"Passed validation: {passed_count} · {opening['title']} — {', '.join(opening['evidence'])}", flush=True)
            debug_lead(action="saved" if inserted else "already saved (updated)", source=f"{employer.name} careers",
                       title=opening["title"], company=employer.name, url=job_url, location=opening["location"],
                       all_locations=opening["locations"], matched_title=matched_title, arrangement=outcome["arrangement"],
                       city=outcome["city"], distance_miles=outcome["miles"], remote_limited_to=sorted(outcome["remote_limited_to"]),
                       posted=opening["posted"])
            if inserted:
                companies_saved += 1
                employer_saved += 1
                employer_saved_total += 1
                print(f"Saved job {companies_saved}/{shared.MAX_SEARCH_RESULTS}: {job_url}")
            else:
                existing_companies += 1
        _board_health.note(employer.name, "Discovered board" if employer.discovered else "Employer board",
                           "ok" if employer_openings else "no matches", len(employer_openings), employer_saved)

        return employer_saved

    known_employers = load_employers()
    if known_employers:
        # Start at a different board each hour so a full quota does not always come from the first few in the list.
        turn = int(time.time() // 3600) % len(known_employers)
        known_employers = known_employers[turn:] + known_employers[:turn]
    known_boards = {config_key(employer.config) for employer in known_employers}
    unreadable_boards = set()
    boards_discovered = 0
    MAX_BOARDS_PER_RUN = 20

    def discover_board(page_url, company_hint=""):
        """A web result on a hiring platform (UltiPro, Greenhouse, ...): search that employer's whole board."""
        nonlocal boards_discovered
        if boards_discovered >= MAX_BOARDS_PER_RUN or shared.stop_requested():
            return
        config = identify_board(page_url)
        if not config:
            unreadable = identify_unreadable(page_url)
            if unreadable and config_key(unreadable) not in unreadable_boards:
                unreadable_boards.add(config_key(unreadable))
                print(f"Found a {unreadable['system'].title()} job board that Job Finder cannot read yet: {page_url[:110]}", flush=True)
            return
        key = config_key(config)
        if key in known_boards:
            return
        known_boards.add(key)
        boards_discovered += 1
        hint = re.sub(r"\s+", " ", str(company_hint or "")).strip()
        generic = not hint or len(hint) > 60 or hint.casefold() in {"job opportunities", "careers", "jobs", "job board"}
        name = board_name(config) if generic or config["system"] != "ultipro" else hint
        config = dict(config, name=name, discovered=True)
        try:
            employer = Employer(config)
        except KeyError:
            return
        print(f"Found {config['system'].title()} job board for {name}; searching its openings...", flush=True)
        save_discovered({k: v for k, v in config.items() if k != "discovered"})
        search_employer(employer, 3, shared.MAX_SEARCH_RESULTS)

    # Job sites that list many employers (the National Labor Exchange, usnlx.com): searched for the titles you
    # typed around each of your places. Their openings go through the same title, location and U.S. checks.
    site_saved_total = 0
    site_cap = max(1, shared.MAX_SEARCH_RESULTS // 3)
    same_job_index = None

    def saved_job_row(url):
        with database.cursor(dictionary=True) as cursor:
            cursor.execute("""SELECT id, name, career_job_title, work_arrangement, city, state, source_url, source_type,
                                     listing_details, is_rejected FROM companies WHERE source_url = %s LIMIT 1""", (url,))
            return cursor.fetchone()

    def index_saved_job(row):
        details = _row_details(row)
        place = details.get("location") or ", ".join(part for part in (row.get("city"), row.get("state")) if part)
        key = match_key(row.get("name"), row.get("career_job_title"), place, row.get("work_arrangement"))
        if key:
            same_job_index.setdefault(key, row)

    def same_saved_job(company, title, location, arrangement, url):
        """The job already saved from another place, when this opening is the same real job (same company and title
        in the same city, or both remote). None when it is new, or when the saved rows can't be read."""
        nonlocal same_job_index
        try:
            if same_job_index is None:
                same_job_index = {}
                with database.cursor(dictionary=True) as cursor:
                    cursor.execute("""SELECT id, name, career_job_title, work_arrangement, city, state, source_url,
                                             source_type, listing_details, is_rejected FROM companies""")
                    for row in cursor.fetchall():
                        index_saved_job(row)
        except Exception:
            same_job_index = {}
            return None
        key = match_key(company, title, location, arrangement)
        row = same_job_index.get(key) if key else None
        return row if row and canonical_url(row.get("source_url")) != canonical_url(url) else None

    def refresh_same_job(row, site, listing):
        """This search found a job we already have (saved from another site): mark it open and freshly checked, link
        this listing as another place it's posted, and fill in what the saved one lacks. Nothing else is changed."""
        details = _row_details(row)
        add_also_on(details, site.name, listing["url"])
        for field, value in (("posted", listing.get("posted")), ("location", listing.get("location")),
                             ("description", str(listing.get("description") or "")[:20000])):
            if value and not details.get(field):
                details[field] = value
        now = datetime.now(timezone.utc)
        with database.cursor() as cursor:
            cursor.execute("""UPDATE companies SET listing_details = %s, job_open_status = 'Open', last_checked = %s,
                              result_updated_at = %s WHERE id = %s""", (json.dumps(details), now, now, row["id"]))
        database.commit()
        row["listing_details"] = json.dumps(details)

    def check_site_listing(site, listing):
        """Filter and save one job-site opening; returns True when it was newly saved."""
        nonlocal candidate_number, results_checked, companies_saved, existing_companies, passed_count, site_saved_total
        job_url, title = listing["url"], listing["title"]
        candidate_number += 1
        results_checked += 1
        print(f"Checking result {candidate_number}: {title[:75]} ({site.name})")
        if not title or job_url in seen_openings:
            return False
        if job_url in rejected_urls:
            record_skip("Previously rejected", job_url, title)
            return False
        seen_openings.add(job_url)
        company = listing["company"] or "Unknown employer"
        if company.casefold() in BLOCKED_COMPANIES:
            debug_skip("Blocked company", job_url, title)
            return False
        if not matching_job_title(title, job_titles):
            record_skip("Title matches none of your job titles", job_url, title)
            return False
        if USA_ONLY and listing["country"] and "united states" not in listing["country"].casefold():
            record_skip("Posting restricts applicants outside the US", job_url, title)
            return False
        opening = {"title": title, "company": company, "description": listing["description"], "type": None,
                   "location": listing["location"], "locations": [listing["location"]], "remote_states": [],
                   "schedule": "", "evidence": [f"{site.name} listing"]}
        outcome = assess_opening(database, opening, capture_page_html(listing), job_url, state, selected_states,
                                 statewide_states, city_targets)
        # The site's own location field ("Aberdeen, MD") is trusted as U.S. proof, as for captured LinkedIn jobs.
        if outcome["skip"] == "US eligibility unverified" and site_location_in_us(listing["location"]):
            outcome["skip"] = None
            outcome["country"] = "United States"
            outcome["location"]["score"] = max(outcome["location"]["score"], shared.USA_CREDIBILITY_THRESHOLD)
        if outcome["skip"]:
            record_skip(outcome["skip"], job_url, title)
            return False
        same = same_saved_job(company, title, listing["location"], outcome["arrangement"], job_url)
        if same:
            if same.get("is_rejected"):
                record_skip("Previously rejected", job_url, title)
                return False
            try:
                refresh_same_job(same, site, listing)
            except Exception as error:
                print(f"Could not update job #{same['id']}: {error}", flush=True)
                return False
            print(f"Already saved as job #{same['id']} ({same.get('source_type') or 'saved'}): updated it and linked this "
                  f"{site.name} listing — {title}", flush=True)
            debug_lead(action="already saved (updated)", source=site.name, title=title, company=company, url=job_url,
                       location=listing["location"], matched_title=None)
            existing_companies += 1
            return False
        matched_title = next((wanted for wanted in job_titles if matching_job_title(title, [wanted])), job_titles[0])
        details = {"posted": listing["posted"], "location": listing["location"], "matched_title": matched_title,
                   "evidence": [f"{site.name} listing", "title matches search"], "external_id": listing["guid"],
                   "remote_limited_to": sorted(outcome["remote_limited_to"]),
                   "verification": f"Listed on the {site.name}; the company site was not checked"}
        inserted = storage.save_company(database, company, title, outcome["detail_score"], site.domain, job_url, job_url,
                                outcome["country"], str(outcome["state_name"] or "")[:100] or None,
                                outcome["location"]["score"], city=outcome["city"], latitude=outcome["lat"],
                                longitude=outcome["lon"], distance_miles=outcome["miles"],
                                work_arrangement=outcome["arrangement"], skills=listing_skills(listing["description"]),
                                source_type=site.name, listing_details=details)
        record_decision(SEARCH_SKIPS_FILE, _skip_decisions, "Passed", job_url, title)
        passed_count += 1
        print(f"Passed validation: {passed_count} · {title} — {company} ({site.name})", flush=True)
        debug_lead(action="saved" if inserted else "already saved (updated)", source=site.name, title=title,
                   company=company, url=job_url, location=listing["location"], matched_title=matched_title)
        if inserted:
            companies_saved += 1
            site_saved_total += 1
            print(f"Saved job {companies_saved}/{shared.MAX_SEARCH_RESULTS}: {job_url}")
            try:
                if same_job_index is not None:
                    new_row = saved_job_row(job_url)
                    if new_row:
                        index_saved_job(new_row)  # a later site in this same search can then link to it
            except Exception:
                pass
        else:
            existing_companies += 1
        return inserted

    def search_job_sites():
        nonlocal site_saved_total
        places = search_places(state, cities, US_STATES)
        for site_class in JOB_SITES:
            if not shared.settings.get("job_sites", {}).get(site_class.name, True):
                print(f"{site_class.name} is turned off in settings.json.")
                _board_health.note(site_class.name, "Job site", "off")
                continue
            site = site_class()
            if not getattr(site, "configured", True):
                print(f"{site.name} needs setup, so it was skipped: {site.setup_hint}")
                _board_health.note(site.name, "Job site", "off", detail=site.setup_hint)
                continue
            found = saved = 0
            site_saved_total = 0  # each job site gets its own share of the search, so one can't use it all up
            print(f"Searching {site.name} for your titles near {', '.join(place for place, _ in places) or 'anywhere'}...")
            try:
                for title in selected_titles:
                    for place, radius in places:
                        if shared.stop_requested() or site_saved_total >= site_cap or companies_saved >= shared.MAX_SEARCH_RESULTS:
                            break
                        for listing in site.search(title, place, radius):
                            found += 1
                            if check_site_listing(site, listing):
                                saved += 1
                            if site_saved_total >= site_cap or companies_saved >= shared.MAX_SEARCH_RESULTS:
                                break
            except SiteBlocked as error:
                print(f"{site.name} asked Job Finder to slow down; leaving it alone for the rest of this search ({error}).")
                _board_health.failed(site.name, "Job site", error)
                continue
            except (requests.RequestException, ValueError, KeyError, TypeError) as error:
                print(f"{site.name} unavailable; continuing: {error}")
                _board_health.failed(site.name, "Job site", error)
                continue
            print(f"{site.name}: {found} openings read in {site.requests} requests, {saved} new saved.")
            _board_health.note(site.name, "Job site", "ok" if found else "no matches", found, saved)

    def search_known_employers():
        try:
            with timed("Employer career boards"):
                for employer in known_employers:
                    search_employer(employer, 2, employer_cap)
                    if employer_saved_total >= employer_cap:
                        break
        except Exception as error:
            print(f"Employer career search stopped early: {error}", flush=True)
        try:
            with timed("Job sites"):
                search_job_sites()
        except Exception as error:
            print(f"Job site search stopped early: {error}", flush=True)

    # Employer boards are searched while Docker and SearXNG start (starting them takes a while), so neither waits
    # for the other. The web search begins only after both have finished.
    employer_thread = threading.Thread(target=search_known_employers, daemon=True)
    employer_thread.start()
    docker_up = False if shared.stop_requested() else docker.start_docker_desktop()
    searxng_up = docker_up and docker.start_searxng()
    employer_thread.join()

    if shared.stop_requested():
        print("Stop requested before the web search started.")
        if docker_up:
            docker.stop_searxng()
            docker.stop_docker_desktop()
        database.close()
        return

    # The remote feed works without Docker. Search it first so a Docker failure
    # does not prevent independent API results from being saved.
    if not docker_up:
        database.close()
        return
    if not searxng_up:
        database.close()
        docker.stop_docker_desktop()
        return
    web_search_started = True

    # Only the titles you typed are searched on the company-board sites (every board found is then read in full).
    search_queries = [f'site:{site} "{title}"' for title in selected_titles for site in shared.JOB_BOARD_SITES]
    city_names = [item.get("city", "").strip() for item in cities if isinstance(item, dict)
                  and item.get("city") and item.get("city", "").strip().casefold() not in US_STATES
                  and not _ZIP_CODE.fullmatch(item.get("city", "").strip())]
    for title in query_titles:
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
            f'"{title}" freelance project {state}',
            f'"{title}" contract gig {state}',
            # Local employers whose openings the big job sites often miss.
            f'"{title}" state government jobs {state}',
            f'"{title}" university jobs {state}',
            f'"{title}" hospital health system careers {state}',
        ])
        for candidate_query in title_queries:
            if candidate_query not in search_queries:
                search_queries.append(candidate_query)


    empty_query_streak = 0
    blocked_message = None

    for search_query in search_queries:
        if companies_saved >= shared.MAX_SEARCH_RESULTS or blocked_message or not docker.check_searxng_timer():
            break

        print()
        print(f'Search variation: "{search_query}"')
        if shared._debug_run is not None:
            shared._debug_run.query(search_query)

        empty_or_repeating_pages = 0
        dry_pages = 0

        query_pages = SITE_QUERY_PAGES if search_query.startswith("site:") else shared.MAX_SEARCH_PAGES
        for page in range(1, query_pages + 1):
            if companies_saved >= shared.MAX_SEARCH_RESULTS or not docker.check_searxng_timer():
                break

            results = search_searxng(search_query, page)
            if shared._debug_run is not None:
                shared._debug_run.query_page(len(results or []))
            if page == 1:
                # Many queries in a row with no results at all means the engines are refusing us.
                empty_query_streak = 0 if results else empty_query_streak + 1
                if empty_query_streak >= EMPTY_QUERY_LIMIT:
                    blocked_message = search_blocked_message(empty_query_streak)
                    print()
                    print(blocked_message, flush=True)
                    break

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
            mostly_seen = len(results) >= 5 and len(new_results) / len(results) < 0.2
            passed_before = passed_count
            seen_result_urls.update(result.get("url") for result in new_results)
            pages_checked += 1
            # PDFs are ignored outright: not fetched, counted, or recorded as skips.
            web_results = [result for result in new_results if not is_pdf_url(result.get("url"))]
            pdfs_ignored = len(new_results) - len(web_results)
            results_checked += len(web_results)
            print(f"Checking search page {page} ({len(web_results)} new results"
                  + (f", {pdfs_ignored} PDFs ignored" if pdfs_ignored else "") + ")...")

            # Download this page's results in parallel; the checks below then read them from the cache.
            prefetch_pages([url for url in (canonical_url(result.get("url") or "") for result in web_results)
                            if url and not is_blocked_domain(get_domain(url)) and not has_blocked_country_domain(get_domain(url))
                            and not cached_skip(_skip_decisions, url)][:24])

            for result in web_results:
                if companies_saved >= shared.MAX_SEARCH_RESULTS or not docker.check_searxng_timer():
                    break
                candidate_number += 1
                title = str(result.get("title") or "")
                url = canonical_url(result.get("url") or "")
                if not url or not is_valid_url(url):
                    continue
                domain = get_domain(url)
                if is_blocked_domain(domain) or has_blocked_country_domain(domain):
                    blocked_sites += 1
                    record_skip("Blocked domain", url, title)
                    continue
                print(f"Checking result {candidate_number}: {title[:75]}", flush=True)
                recent_skip = cached_skip(_skip_decisions, url)
                if recent_skip:
                    print(f"Skipped (recent check: {recent_skip['reason']}): {title[:70]} {url[:120]}", flush=True)
                    continue
                discover_board(url, title)
                websites_checked += 1
                landing = fetching.safe_request(url)
                if landing is None:
                    record_skip("Page unavailable", url, title)
                    continue
                landing_soup = BeautifulSoup(landing.text, "html.parser")
                if is_directory_or_marketplace_result(domain, title, landing.text):
                    record_skip("Directory or marketplace page", url, title)
                    continue
                if is_article_page(landing_soup) or is_student_employment_overview(url, landing.text):
                    record_skip("Article or student employment guide", url, title)
                    continue
                company_name = extract_company_name(landing_soup, title, domain)
                if company_name.strip().casefold() in BLOCKED_COMPANIES:
                    debug_skip("Blocked company", url, title)
                    continue
                # Follow the site's career navigation, then individual job links.
                # Each URL is fetched at most once per search and failures stay local.
                pending = [url]
                for link in landing_soup.find_all("a", href=True):
                    marker = f"{link.get_text(' ', strip=True)} {link['href']}".casefold()
                    if any(term in marker for term in ("career", "job openings", "open positions", "join our team")):
                        candidate = canonical_url(urljoin(url, link["href"]))
                        if candidate and (get_domain(candidate) == domain or any(get_domain(candidate).endswith(ats) for ats in ATS_DOMAINS)):
                            pending.append(candidate)
                pending.extend(job_links(url, landing.text))
                for board_url in pending[:10]:
                    pending.extend(public_board_links(board_url, job_titles))
                checked = set()
                while pending and len(checked) < 18 and companies_saved < shared.MAX_SEARCH_RESULTS and docker.check_searxng_timer():
                    page_url = pending.pop(0)
                    if page_url != url:
                        discover_board(page_url, company_name)
                    if page_url in checked or page_url in fetched_pages or is_pdf_url(page_url):
                        continue
                    checked.add(page_url)
                    fetched_pages.add(page_url)
                    page = landing if page_url == url else fetching.safe_request(page_url)
                    if page is None:
                        record_skip("Job page unavailable", page_url, title)
                        continue
                    openings = extract_jobs(page.url, page.text, job_titles)
                    if not openings and len(checked) < 8:
                        pending.extend(job_links(page.url, page.text, limit=24))
                        pending.extend(pagination_links(page.url, page.text))
                        pending.extend(public_board_links(page.url, job_titles))
                        prefetch = list(dict.fromkeys(candidate for candidate in pending
                                                       if candidate not in checked and candidate not in fetched_pages))[:4]
                        prefetch_pages(prefetch, workers=4)
                    elif not openings:
                        record_skip("No matching individual opening", page.url, title)
                    for opening in openings:
                        job_url = canonical_url(opening["url"])
                        if not job_url or job_url in seen_openings:
                            continue
                        if job_url in rejected_urls:
                            debug_skip("Previously rejected", job_url, opening["title"])
                            continue
                        seen_openings.add(job_url)
                        if is_blocked_domain(get_domain(job_url)):
                            debug_skip("Blocked domain", job_url, opening["title"])
                            continue
                        name = tidy_company_name(opening["company"]) or company_name
                        if name.casefold() in BLOCKED_COMPANIES:
                            debug_skip("Blocked company", job_url, opening["title"])
                            continue
                        details = {key: opening.get(key) for key in ("schedule", "salary", "posted", "evidence", "location")}
                        details["source"] = url
                        details["matched_title"] = next((wanted for wanted in job_titles if matching_job_title(opening["title"], [wanted])), job_titles[0])
                        outcome = assess_opening(database, opening, page.text, job_url, state, selected_states,
                                                 statewide_states, city_targets)
                        if outcome["skip"]:
                            record_skip(outcome["skip"], job_url, opening["title"])
                            continue
                        arrangement, location, detail_score = outcome["arrangement"], outcome["location"], outcome["detail_score"]
                        country, state_name, remote_limited_to = outcome["country"], outcome["state_name"], outcome["remote_limited_to"]
                        details["remote_limited_to"] = sorted(remote_limited_to)
                        city, lat, lon, miles = outcome["city"], outcome["lat"], outcome["lon"], outcome["miles"]
                        # A repost on a job board can outlive the real opening; check where Apply leads.
                        closed = apply_link_closed(job_url, page.text) if is_third_party(job_url, name) else None
                        if closed:
                            record_skip("Job is closed (expired)", job_url, opening["title"])
                            debug_skip(f"closed: {closed['reason']}", closed["apply_link"], opening["title"])
                            continue
                        # A listing found on a job board should link to the employer's own careers page.
                        employer_notes = []
                        employer = None
                        if is_third_party(job_url, name):
                            employer = find_employer_site(
                                name, opening["title"], job_url, page.text, job_titles,
                                location_hint=opening["location"] or f"{city or ''} {state_name or ''}".strip(),
                                notes=employer_notes)
                        row_domain, row_career_url = get_domain(job_url), job_url
                        if employer:
                            row_domain = employer["domain"]
                            # View goes to the job itself; the employer's careers page is shown separately.
                            row_career_url = employer["posting_url"] or job_url
                            details["employer_site"] = {"domain": employer["domain"],
                                                        "careers_url": employer["careers_url"],
                                                        "method": employer["method"],
                                                        "posting_found": bool(employer["posting_url"])}
                            details["original_source"] = job_url
                            details["evidence"] = list(details.get("evidence") or []) + employer["evidence"]
                            print(f"Employer site: {employer['domain']} ({employer['method']}) -> {row_career_url}", flush=True)
                        elif employer_notes:
                            print("Employer site not found: " + "; ".join(employer_notes), flush=True)
                        # The job board is only a copy: use the company's own hiring-board posting when it has one.
                        board_posting = company_board_posting(name, opening["title"]) if is_third_party(job_url, name) else None
                        if board_posting:
                            row_career_url = board_posting["url"]
                            detail_score = max(detail_score, OFFICIAL_BOARD_CREDIBILITY)
                            details["ats_posting"] = {"system": board_posting["system"], "url": board_posting["url"]}
                            details.setdefault("original_source", job_url)
                            details["evidence"] = list(details.get("evidence") or []) + [
                                f"posting found on the company's own {board_posting['system'].title()} board"]
                            print(f"Company's own posting: {board_posting['url']}", flush=True)
                        details["verification"] = verification_label(details, None, name, job_url)
                        inserted = storage.save_company(database, name, opening["title"], detail_score,
                                                row_domain, row_career_url, job_url, country,
                                                str(state_name or "")[:100] or None, location["score"], city=city,
                                                latitude=lat, longitude=lon, distance_miles=miles,
                                                work_arrangement=arrangement, skills=listing_skills(page.text),
                                                listing_details=details)
                        record_decision(SEARCH_SKIPS_FILE, _skip_decisions, "Passed", job_url, opening["title"])
                        passed_count += 1
                        print(f"Passed validation: {passed_count} · {opening['title']} — {', '.join(details.get('evidence') or ['individual opening'])}", flush=True)
                        debug_lead(action="saved" if inserted else "already saved (updated)", title=opening["title"],
                                   company=name, url=job_url, view_link=row_career_url, domain=row_domain,
                                   matched_title=details["matched_title"], evidence=details.get("evidence"),
                                   found_by_query=search_query, search_result={"title": title, "url": url},
                                   listing_page=page.url, location=opening["location"],
                                   location_check={"score": location["score"], "state": location["state"],
                                                   "country": country, "evidence": location.get("evidence")},
                                   arrangement=arrangement, city=city, distance_miles=miles,
                                   remote_limited_to=sorted(remote_limited_to),
                                   employer_site=details.get("employer_site"), employer_notes=employer_notes,
                                   career_credibility=detail_score, salary=details.get("salary"),
                                   posted=details.get("posted"))
                        if inserted:
                            companies_saved += 1
                            print(f"Saved job {companies_saved}/{shared.MAX_SEARCH_RESULTS}: {job_url}")
                        else:
                            existing_companies += 1
                if not checked:
                    no_career_page += 1

            dry_pages = 0 if passed_count > passed_before else dry_pages + 1
            if mostly_seen:
                print("Most results on this page were already seen; moving to the next query.", flush=True)
                break
            if dry_pages >= 3:
                print("Three search pages in a row gave no matching jobs; moving to the next query.", flush=True)
                break

    if companies_saved < shared.MAX_SEARCH_RESULTS:
        print()
        print(
            f"Search exhausted after saving {companies_saved}/"
            f"{shared.MAX_SEARCH_RESULTS} viable companies."
        )

    database.close()

    print()
    print("=" * 60)
    print("SEARCH COMPLETE")
    print()
    stop_reason = docker.search_stop_reason(companies_saved, blocked_message)
    print(f"Stop reason: {stop_reason}")
    if shared._debug_run is not None:
        shared._debug_run.stop_reason = stop_reason
        shared._debug_run.data["summary"] = {
            "results_checked": results_checked, "search_pages_checked": pages_checked,
            "websites_checked": websites_checked, "passed_validation": passed_count,
            "saved_new": companies_saved, "already_saved": existing_companies,
            "search_engines_blocking": bool(blocked_message), "seconds_spent": timing_summary()}

    print(f"Search results checked: {results_checked}")
    print(f"Search pages checked: {pages_checked}")
    for timing_name, timing_seconds in timing_summary().items():
        print(f"Time: {timing_name}: {timing_seconds:.0f}s")

    print(f"Aggregators skipped: " f"{blocked_sites}")

    print(f"Non-US domains skipped: " f"{blocked_countries}")

    print(f"Duplicates skipped: " f"{duplicates}")

    print(f"Existing companies updated: " f"{existing_companies}")

    print(f"Websites checked: " f"{websites_checked}")

    print(f"No career page: " f"{no_career_page}")


    print(f"Database saves: " f"{companies_saved}")


def main(job_title=None, state=None, cities_json=None, max_new=None):
    global web_search_started
    web_search_started = False
    clear_employer_cache()
    clear_ats_cache()
    _board_health.clear()
    shared._debug_run = DebugRun(shared.SEARCH_DEBUG_FILE, "replacement" if max_new == 1 else "search", JOB_FINDER_VERSION)
    try:
        _run_search(job_title=job_title, state=state, cities_json=cities_json, max_new=max_new)
    except BaseException as error:
        shared._debug_run.stop_reason = f"stopped by {type(error).__name__}: {error}"
        raise
    finally:
        # Also runs after a crash or stop request, so SearXNG and Docker are not left running.
        if web_search_started:
            docker.stop_searxng()
            docker.stop_docker_desktop()
        shared._debug_run.finish(shared._debug_run.stop_reason or "ended early (setup failed or a stop was requested)")
        shared._debug_run = None
        _board_health.write(BOARD_HEALTH_FILE, "replacement" if max_new == 1 else "search")


def parse_arguments():
    parser = argparse.ArgumentParser(description="Personal Job Finder")
    parser.add_argument("--update-existing", action="store_true")
    parser.add_argument("--update-ids", default=None)
    parser.add_argument("--import-captures", action="store_true",
                        help="Import jobs saved by the Web Job Scraper extension from web-job-scraper\\searches")
    parser.add_argument("--max-new", type=int, choices=range(1, 11), default=None)
    parser.add_argument("--job-title", default=None)
    parser.add_argument("--state", default=None)
    parser.add_argument("--cities-json", default=None)
    return parser.parse_args()


if __name__ == "__main__":
    for output in (sys.stdout, sys.stderr):
        if hasattr(output, "reconfigure"):
            output.reconfigure(encoding="utf-8", errors="backslashreplace")
    args = parse_arguments()

    if args.import_captures:
        import_captures()
    elif args.update_existing:
        update_existing_results([int(value) for value in args.update_ids.split(",") if value.isdecimal()] if args.update_ids is not None else None)
    else:
        main(job_title=args.job_title, state=args.state, cities_json=args.cities_json, max_new=args.max_new)
