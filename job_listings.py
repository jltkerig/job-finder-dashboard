"""Conservative, source-aware extraction of individual job openings."""
import json
import re
from datetime import date, datetime, timezone
from urllib.parse import urljoin, urlparse, urlunparse, parse_qsl, urlencode

from bs4 import BeautifulSoup


ATS_HOSTS = ("greenhouse.io", "lever.co", "ashbyhq.com", "workdayjobs.com", "smartrecruiters.com", "icims.com", "jobvite.com", "bamboohr.com")
NON_JOB_PATH = re.compile(r"/(?:news|blog|stories|magazine|services?|financial-aid|financialaid|types-of-aid|work-study)(?:/|$)", re.I)
JOB_PATH = re.compile(r"/(?:jobs?|positions?|openings?|careers?)/(?:[^/?#]+/)*[^/?#]+/?$", re.I)
SKIP_QUERY_KEYS = {"utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term", "fbclid", "gclid"}


def canonical_url(url):
    parsed = urlparse(url or "")
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return ""
    query = urlencode(sorted((key, value) for key, value in parse_qsl(parsed.query)
                           if key.lower() not in SKIP_QUERY_KEYS))
    path = parsed.path.rstrip("/") or "/"
    return urlunparse((parsed.scheme.lower(), parsed.netloc.lower(), path, "", query, ""))


def _nodes(value):
    if isinstance(value, list):
        for item in value:
            yield from _nodes(item)
    elif isinstance(value, dict):
        yield value
        for item in value.get("@graph", []):
            yield from _nodes(item)


def _is_type(node, name):
    kinds = node.get("@type") or []
    if isinstance(kinds, str):
        kinds = [kinds]
    return any(str(kind).split("/")[-1].casefold() == name.casefold() for kind in kinds)


def _tokens(value):
    text = str(value or "").casefold()
    for old, new in ((r"front[ -]?end", "frontend"), (r"back[ -]?end", "backend"),
                     (r"full[ -]?stack", "fullstack")):
        text = re.sub(r"\b" + old + r"\b", new, text)
    return set(re.findall(r"[a-z0-9]+", text)) - {"senior", "junior", "remote", "hybrid", "job", "jobs", "the", "a"}


def matching_title(title, wanted):
    actual = _tokens(title)
    return any(words and (words <= actual or (len(words) >= 3 and len(words & actual) >= len(words) - 1))
               for words in (_tokens(w) for w in wanted))


def excludes_us(location, description=""):
    """Reject explicit non-US-only restrictions; ambiguous locations stay unverified."""
    location = str(location or "")
    evidence = f"{location} {str(description or '')[:1500]}".casefold()
    if re.search(r"\b(?:united states|usa|u\.s\.|us only|worldwide|anywhere|global)\b", location.casefold()):
        return False
    non_us = r"\b(?:canada|united kingdom|uk|germany|france|india|australia|philippines|brazil|europe|emea)\b"
    return bool(re.search(non_us, location.casefold()) or
                re.search(r"(?:remote|applicants?|candidates?).{0,45}(?:only|must be|based in).{0,35}" + non_us, evidence))


def _plain(value):
    if isinstance(value, dict):
        return str(value.get("name") or value.get("value") or "")
    return str(value or "")


def _locations(value):
    if isinstance(value, list):
        return ", ".join(filter(None, (_locations(item) for item in value)))
    if not isinstance(value, dict):
        return _plain(value)
    address = value.get("address") or value
    if isinstance(address, dict):
        return ", ".join(filter(None, (_plain(address.get(key)) for key in
                                      ("addressLocality", "addressRegion", "addressCountry"))))
    return _plain(address)


def _salary(value):
    if isinstance(value, list):
        value = value[0] if value else None
    if not isinstance(value, dict):
        return ""
    currency = value.get("currency") or ""
    amount = value.get("value") or value
    if isinstance(amount, dict):
        low = amount.get("minValue") or amount.get("value")
        high = amount.get("maxValue")
        unit = amount.get("unitText") or ""
    else:
        low, high, unit = amount, None, ""
    if not low:
        return ""
    symbol = "$" if currency == "USD" else (currency + " " if currency else "")
    amount_text = f"{symbol}{low}{'–' + symbol + str(high) if high and str(high) != str(low) else ''}"
    unit_text = f" / {unit.casefold()}" if unit else ""
    return (amount_text + unit_text)[:120]


def _freshness(value):
    if not value:
        return None
    try:
        posted = datetime.fromisoformat(str(value).replace("Z", "+00:00")).date()
        if posted > datetime.now(timezone.utc).date():
            return None
        return posted.isoformat()
    except ValueError:
        return None


def extract_jobs(url, html, wanted):
    """Only return individual postings with a matching title and direct URL."""
    soup = BeautifulSoup(html, "html.parser")
    if soup.find("meta", attrs={"property": "og:type", "content": "article"}):
        return []
    results = []
    seen = set()
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or script.get_text())
        except (ValueError, TypeError):
            continue
        for node in _nodes(data):
            if not _is_type(node, "JobPosting"):
                continue
            title = _plain(node.get("title") or node.get("name")).strip()
            direct = canonical_url(urljoin(url, _plain(node.get("url") or node.get("applyUrl") or url)))
            if not matching_title(title, wanted) or not direct or direct in seen:
                continue
            expiry = _freshness(node.get("validThrough"))
            if expiry and expiry < date.today().isoformat():
                continue
            location = _locations(node.get("jobLocation"))
            remote = any(token in _plain(node.get("jobLocationType")).casefold() for token in ("remote", "telecommute"))
            if remote:
                location = _locations(node.get("applicantLocationRequirements")) or location or "Remote"
            if re.search(r"\bremote\b", location, re.I):
                remote = True
            employer = _plain(node.get("hiringOrganization"))
            description = BeautifulSoup(_plain(node.get("description")), "html.parser").get_text(" ", strip=True)
            work_text = f"{title} {location} {description[:2500]}"
            location_type = _plain(node.get("jobLocationType"))
            arrangement = ("Hybrid" if re.search(r"\bhybrid\b", f"{location_type} {work_text}", re.I)
                           else "Remote" if remote or re.search(r"\b(?:remote|telecommut(?:e|ing)|work from home|wfh)\b", f"{title} {location}", re.I)
                           or re.search(r"\b(?:fully remote|remote position|remote role|remote work|work from home|telecommut(?:e|ing))\b", description[:2500], re.I)
                           else "Onsite" if re.search(r"\b(?:on[- ]?site|in[- ]?office|in[- ]?person|office[- ]based)\b", work_text, re.I)
                           else None)
            evidence = ["JobPosting data", "title matches search", "direct listing link"]
            posted = _freshness(node.get("datePosted"))
            if posted:
                evidence.append("posted " + posted)
            schedule = _plain(node.get("employmentType"))
            if re.search(r"\b(?:freelanc(?:e|er)|gig|project[- ]based|independent contractor)\b", f"{schedule} {title} {description[:2500]}", re.I):
                schedule = "Freelance / Gig"
            results.append(dict(title=title[:255], url=direct, company=employer[:255], location=location[:100],
                                type=arrangement, schedule=schedule[:100],
                                salary=_salary(node.get("baseSalary")), posted=posted, evidence=evidence,
                                description=description))
            seen.add(direct)
    if results or NON_JOB_PATH.search(urlparse(url).path):
        return results
    # A page without structured data qualifies only if it looks like one job
    # and has its own apply action. Site-wide navigation links do not count.
    heading = soup.find("h1")
    title = heading.get_text(" ", strip=True) if heading else ""
    if not matching_title(title, wanted):
        return []
    apply = next((link for link in soup.find_all("a", href=True)
                  if re.fullmatch(r"(?:apply(?: now| for (?:this )?job)?|submit application)",
                                  link.get_text(" ", strip=True), re.I)), None)
    if not apply or not (JOB_PATH.search(urlparse(url).path) or any(urlparse(url).hostname.endswith(h) for h in ATS_HOSTS)):
        return []
    direct = canonical_url(url)
    page_text=soup.get_text(" ", strip=True)[:20000]
    project_work=bool(re.search(r"\b(?:freelanc(?:e|er)|gig|project[- ]based|independent contractor)\b", f"{title} {page_text[:2500]}", re.I))
    return [dict(title=title[:255], url=direct, company="", location="", type=None,
                 schedule="Freelance / Gig" if project_work else "", salary="", posted=None,
                 evidence=["job detail page", "title matches search", "apply action"],
                 description=page_text)]


def job_links(url, html, limit=24):
    """Find likely posting URLs on a careers page, not generic footer links."""
    soup = BeautifulSoup(html, "html.parser")
    origin = urlparse(url).hostname or ""
    ranked = []
    for link in soup.find_all("a", href=True):
        href = canonical_url(urljoin(url, link["href"]))
        host = urlparse(href).hostname or ""
        if not href or (host != origin and not any(host.endswith(h) for h in ATS_HOSTS)):
            continue
        path = urlparse(href).path
        if NON_JOB_PATH.search(path):
            continue
        text = link.get_text(" ", strip=True)
        if JOB_PATH.search(path) or ("job" in path.casefold() and len(_tokens(text)) >= 2) or (any(host.endswith(h) for h in ATS_HOSTS) and len(_tokens(text)) >= 2):
            ranked.append(href)
    return list(dict.fromkeys(ranked))[:limit]


def pagination_links(url, html, limit=3):
    soup = BeautifulSoup(html, "html.parser")
    origin = urlparse(url).hostname
    result = []
    for link in soup.find_all("a", href=True):
        label = link.get_text(" ", strip=True).casefold()
        if label not in {"next", "next page", "2", "3"} and "next" not in (link.get("rel") or []):
            continue
        target = canonical_url(urljoin(url, link["href"]))
        if target and urlparse(target).hostname == origin and target != canonical_url(url):
            result.append(target)
    return list(dict.fromkeys(result))[:limit]
