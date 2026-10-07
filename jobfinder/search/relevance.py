"""Does a lead fit your titles and places, and which employer's own site does it belong to?"""

import json
from types import SimpleNamespace

from mysql.connector import Error
import re

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


# Describing words for the digital and visual design work these searches are about: a related title made only of
# these ("Interface Designer", "Website Designer") is kept even when it shares no word with the typed title.
DIGITAL_WORDS = {"web", "website", "internet", "site", "digital", "interface", "experience", "ux", "ui", "user",
                 "interaction", "visual", "graphic", "graphics", "multimedia", "media", "content", "front",
                 "end", "frontend", "page", "online", "brand", "creative", "production", "art"}


def _title_words(title):
    return {word for word in re.split(r"[\s/&,()-]+", title.casefold()) if word}


def expand_job_titles(titles):
    """The typed titles plus up to two related O*NET titles for each. A related title must keep the role ("Designer")
    and share a describing word with one of the typed titles ("UX/UI Designer" for "UX Designer"); a bare role or an
    unrelated kind ("Fur Designer", "Textile Designer") would match far too much."""
    expanded = list(titles)
    seen = {title.casefold() for title in titles}
    for title in titles:
        role = title.casefold().split()[-1]
        describing = {word for typed in titles for word in _title_words(typed)} - {role}
        added = 0
        for suggestion in related_title_suggestions(title, limit=12):
            words = _title_words(suggestion)
            kind = words - {role}
            if (role in words and kind and kind <= describing | DIGITAL_WORDS
                    and suggestion.casefold() not in seen):
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


OTHER_FIELD = re.compile(r"\b(?:engineer\w*|sewer|highway|civil|bim|cad|computer-aided|technical designer|"
                         r"instructional|extension specialist|full[- ]?stack|java|node(?:\.js)?|"
                         r"telecommunications)\b", re.I)
DESIGN_FIELD = re.compile(r"\b(?:design\w*|web\w*|digital|graphic\w*|visual|content|creative|ux|ui|front[- ]?end|"
                          r"producer|production|brand|interactive|multimedia|marketing)\b", re.I)


def is_irrelevant_lead(company, details, wanted):
    """An unsaved lead whose title matches none of the user's titles. Judged again every time: the title stored when
    it was saved may come from an older, looser match (a bare "Designer" let in sewer and highway engineers)."""
    title = (company.get("career_job_title") or "").strip()
    if not (wanted and title and not company.get("is_kept")) or matching_job_title(title, wanted):
        return False
    # A title matched when it was saved still counts while it is one of the current titles; an old, looser one
    # (a bare "Designer") doesn't.
    if str(details.get("matched_title") or "").casefold() in {str(t).casefold() for t in wanted}:
        return False
    # No title matches: reject only what is clearly another field; design-adjacent roles (UX/UI, product, creative,
    # digital content or producer) stay for the user to judge.
    return bool(OTHER_FIELD.search(title) or not DESIGN_FIELD.search(title))


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


BROWSER_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                                 "Chrome/126.0 Safari/537.36", "Accept": "text/html,application/xhtml+xml"}


def fetch_text(url, max_bytes=6_000_000):
    """Any text file (sitemap.xml, robots.txt) as an object with .url and .text, or None; read politely and size-capped."""
    if not is_valid_url(url):
        return None
    try:
        wait_for_host(url)
        response = requests.get(url, headers=HEADERS, timeout=TIMEOUT, stream=True)
        if response.status_code == 403:
            # Some firewalls (CloudFront) refuse any non-browser agent even on public job pages: ask once more as a
            # browser would.
            response.close()
            response = requests.get(url, headers=BROWSER_HEADERS, timeout=TIMEOUT, stream=True)
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
