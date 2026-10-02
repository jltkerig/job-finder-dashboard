"""Working out which company a posting belongs to, and how much to trust the page it was found on."""

import json
import re

from jobfinder.search.fetching import get_domain
from jobfinder.sources.ats_discovery import identify as identify_board
from jobfinder.sources.ats_lookup import find_ats_posting
from jobfinder.sources.employer_jobs import SOURCE_TYPE as EMPLOYER_SOURCE, Http as EmployerHttp
from jobfinder.sources.employer_site import is_third_party


_ats_http = None


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
