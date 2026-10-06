"""Visiting a company's own website to find its careers page and judge whether it is a real employer."""

import re
import time
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
import requests

from jobfinder.search import session
from jobfinder.search import fetching
from jobfinder.search import shared
from jobfinder.search.company_names import _JOB_TITLE_WORDS, extract_company_name
from jobfinder.search.fetching import get_domain, has_blocked_country_domain, is_blocked_domain, is_valid_url
from jobfinder.search.shared import HEADERS, MAX_DISCOVERY_PAGES, MAX_HTML_SIZE, TIMEOUT
from jobfinder.search.usa_location import analyze_usa_location, merge_location_data

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

    if not shared.update_existing_mode and not session.web_search_ok():
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
