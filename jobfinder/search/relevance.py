"""Does a lead fit your titles and places, and which employer's own site does it belong to?"""

import json
from types import SimpleNamespace

from mysql.connector import Error
import requests

from jobfinder.profiles.onet_data import related_title_suggestions
from jobfinder.search import fetching
from jobfinder.search import geo
from jobfinder.search.company_site import ATS_DOMAINS, DIRECTORY_MARKETPLACE_DOMAINS, SOCIAL_DOMAINS, score_career_page
from jobfinder.search.fetching import has_blocked_country_domain, is_blocked_domain, is_valid_url, wait_for_host
from jobfinder.search.searching import search_web
from jobfinder.search.shared import HEADERS, TIMEOUT, USA_ONLY, is_internship
from jobfinder.search.usa_location import selected_state_codes
from jobfinder.sources.ats_discovery import identify as identify_board
from jobfinder.sources.closed_jobs import listing_closed
from jobfinder.sources.employer_jobs import Employer
from jobfinder.sources.employer_site import resolve_employer_site
from jobfinder.sources.job_feeds import FEED_NAMES
from jobfinder.sources.job_listings import excludes_us, matching_title as matching_job_title


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
    return geo.prepare_city_targets(database, latest[0] or "", cities), latest[0] or ""


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


def decode_page(content, declared=None):
    """Page bytes as text: UTF-8 when they are valid UTF-8, otherwise the encoding the server declared, or Windows-1252
    (what most older sites mean by "ISO-8859-1"), so curly quotes and dashes don't turn into "�"."""
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError:
        pass
    for encoding in (declared, "cp1252"):
        if encoding and encoding.lower() not in {"utf-8", "utf8", "iso-8859-1", "latin-1"}:
            try:
                return content.decode(encoding)
            except (LookupError, UnicodeDecodeError):
                continue
    return content.decode("cp1252", errors="replace")


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
        return SimpleNamespace(url=response.url, text=decode_page(content, response.encoding))
    except requests.RequestException:
        return None


def find_employer_site(name, job_title, job_url, page_html, titles, location_hint="", allow_search=True, notes=None):
    return resolve_employer_site(
        name, job_title, job_url, page_html, titles, fetch=fetching.safe_request,
        search=search_web if allow_search else None, score_page=score_career_page,
        is_excluded=is_excluded_employer_host, location_hint=location_hint,
        allow_search=allow_search, notes=notes, fetch_raw=fetch_text)
