"""Conservative, source-aware extraction of individual job openings."""
import json
import re
from datetime import date, datetime, timezone
from urllib.parse import urljoin, urlparse, urlunparse, parse_qsl, urlencode

from bs4 import BeautifulSoup


ATS_HOSTS = ("greenhouse.io", "lever.co", "ashbyhq.com", "workdayjobs.com", "smartrecruiters.com", "icims.com", "jobvite.com", "bamboohr.com")
_ALWAYS_NON_JOB = re.compile(r"/(?:news|blog|stories|magazine|financial-aid|financialaid|types-of-aid|work-study)(?:/|$)", re.I)
_SERVICES_PATH = re.compile(r"/services?(?:/|$)", re.I)
_JOB_CONTEXT = re.compile(r"/(?:jobs?|careers?|positions?|openings?|vacanc(?:y|ies)|opportunit(?:y|ies)|employment)(?:/|$)", re.I)


class _NonJobPath:
    """Paths that are articles or service pages. A /services/ folder under /careers/ still holds real jobs."""

    def search(self, path):
        path = str(path or "")
        if _ALWAYS_NON_JOB.search(path):
            return True
        return bool(_SERVICES_PATH.search(path) and not _JOB_CONTEXT.search(path))


NON_JOB_PATH = _NonJobPath()
JOB_PATH = re.compile(r"/(?:jobs?|positions?|openings?|careers?)/(?:[^/?#]+/)*[^/?#]+/?$", re.I)
SKIP_QUERY_KEYS = {"utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term", "utm_id", "fbclid", "gclid",
                   "applyrequired", "trk", "trackingid", "refid", "mc_cid", "mc_eid", "gh_src", "lever-source"}


def is_pdf_url(url):
    """PDFs are never job pages Job Finder can read, so searches skip them unfetched."""
    return urlparse(url or "").path.casefold().endswith(".pdf")


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


_DROPPED_WORDS = {"senior", "junior", "remote", "hybrid", "job", "jobs", "the", "a"}
# Words that put a title in a different trade from a designer/developer search.
OFF_FIELD_WORDS = {"interior", "landscape", "fashion", "apparel", "industrial", "mechanical", "electrical", "structural",
                   "architectural", "jewelry", "floral", "kitchen", "furniture", "merchandising", "merchandiser"}
# Extra words that may sit around or between the searched words without changing the job.
COMPATIBLE_EXTRA_WORDS = {
    "ux", "ui", "digital", "creative", "visual", "interaction", "interactive", "graphic", "content", "product", "brand",
    "marketing", "email", "motion", "print", "wordpress", "and", "or", "frontend", "front", "end", "specialist",
    "associate", "lead", "staff", "principal", "intern", "web", "website", "ii", "iii", "iv", "v", "i"}


def _stem(word):
    """One form per word, so "graphic design" matches "Graphic Designer(s)" and "web" matches "website"."""
    if word == "website":
        return "web"
    for suffix in ("ers", "er", "s"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 4:
            return word[: -len(suffix)]
    return word


def _token_list(value):
    text = str(value or "").casefold()
    for old, new in ((r"front[ -]?end", "frontend"), (r"back[ -]?end", "backend"),
                     (r"full[ -]?stack", "fullstack")):
        text = re.sub(r"\b" + old + r"\b", new, text)
    return [_stem(word) for word in re.findall(r"[a-z0-9]+", text) if word not in _DROPPED_WORDS]


def _tokens(value):
    return set(_token_list(value))


_OFF_FIELD_STEMS = {_stem(word) for word in OFF_FIELD_WORDS}
_COMPATIBLE_STEMS = {_stem(word) for word in COMPATIBLE_EXTRA_WORDS}


# Page titles that are not one job: directories ("Find a Web Designer in Maryland"), job lists ("Remote Content Designer
# Jobs in the US"), agency service pages ("WordPress web design in Gaithersburg") and salary or how-to pages.
NOT_A_JOB = re.compile(r"^\s*(?:find|hire|compare|top\s+\d+|best)\s+(?:an?\s+|the\s+)?|\bjobs\b|\bnear\s+me\b|\bsalar(?:y|ies)\b|"
                       r"\bhow\s+to\b|\b(?:web|website|graphic|logo|wordpress)\s+design\s+(?:in|near|services?|company|"
                       r"agency|packages?)\b|\bhiring\s+(?:an?\s+)?(?:freelance\s+)?\w+\s+near\b", re.I)


def looks_like_not_a_job(title):
    return bool(NOT_A_JOB.search(str(title or "")))


def matching_title(title, wanted):
    """The searched words must appear together (Web Producer is not Web Series Producer)."""
    if looks_like_not_a_job(title):
        return False
    actual_list = _token_list(title)
    actual = set(actual_list)
    # "Lead, Digital Designer (Apparel & Footwear)": the bracketed department is not part of the job's name.
    core = set(_token_list(re.split(r"\s[-–|]\s|\(", str(title or ""))[0]))
    for phrase in wanted:
        words_list = _token_list(phrase)
        words = set(words_list)
        if not words or (core & _OFF_FIELD_STEMS) - words:
            continue
        size = len(words_list)
        if any(actual_list[i:i + size] == words_list for i in range(len(actual_list) - size + 1)):
            return True
        extras = actual - words
        if words <= actual and all(word in _COMPATIBLE_STEMS or word.isdigit() for word in extras):
            return True
        # Two of three words are enough ("Senior Visual Graphic Designer" for "Visual Graphic Designer"), but the role must
        # be one of them: "Data Platform & Governance" manager is not a "Data Governance Analyst".
        if size >= 3 and len(words & actual) >= size - 1 and words_list[-1] in actual:
            return True
    return False


# A trailing ISO country code ("London, GB"). Codes that are also US state abbreviations
# (CA, DE, IN, ...) are left out on purpose: "Wilmington, DE" is Delaware.
NON_US_COUNTRY_CODE = re.compile(
    r",\s*(?:gb|uk|fr|ie|au|nz|jp|cn|br|mx|za|it|es|nl|se|no|dk|fi|pl|pt|ch|at|be|sg|ae|ro|cz|hu|gr|tr|ph|ng|ke|"
    r"eg|kr|tw|hk|th|vn|my|pk|bd|ua|ru)\s*$", re.I)


def excludes_us(location, description=""):
    """Reject explicit non-US-only restrictions; ambiguous locations stay unverified."""
    location = str(location or "")
    evidence = f"{location} {str(description or '')[:1500]}".casefold()
    # "u.s." ends in punctuation, so a trailing \b would never match it.
    if re.search(r"\b(?:united states|usa|us only|worldwide|anywhere|global)\b|\bu\.s\.(?!\w)", location.casefold()):
        return False
    countries = (r"canada|united kingdom|uk|great britain|england|scotland|wales|ireland|germany|france|spain|"
                 r"italy|netherlands|poland|portugal|sweden|norway|denmark|finland|switzerland|austria|belgium|india|"
                 r"australia|new zealand|philippines|brazil|mexico|japan|china|singapore|israel|europe|emea|apac|latam")
    # In the description the country must be the thing the applicant has to satisfy
    # ("must be based in Canada"), not just a place mentioned later in the sentence.
    requirement = (r"(?:remote|applicants?|candidates?).{0,45}?(?:only|must be|must reside|must live|based|located|"
                   r"residing)\s+(?:(?:based|located|residing)\s+)?(?:in|of)\s+(?:the\s+)?(?:" + countries + r")\b"
                   r"|\b(?:residents?|citizens?)\s+of\s+(?:the\s+)?(?:" + countries + r")\b"
                   r"|\b(?:" + countries + r")\b[\s\-–,()]*(?:residents?\s+)?only\b")
    return bool(re.search(r"\b(?:" + countries + r")\b", location.casefold()) or NON_US_COUNTRY_CODE.search(location)
                or re.search(requirement, evidence))


_HYBRID_WORDS = re.compile(r"\b(?:hybrid|part(?:ly|ially) remote|split between (?:home|remote) and (?:the )?office)\b", re.I)
_REMOTE_WORDS = re.compile(r"\b(?:remote|work(?:ing)? from home|wfh|telecommut(?:e|ing)|home[ -]based|distributed team)\b", re.I)
_ONSITE_WORDS = re.compile(r"\b(?:on[ -]?site|in[ -]?office|in[ -]?person|office[ -]based|on[ -]?premises)\b", re.I)
_DESCRIBED_REMOTE = re.compile(
    r"\b(?:fully remote|100% remote|remote[- ]first|remote (?:position|role|job|opportunity)"
    r"|(?:this|the) (?:role|position|job|opportunity) is (?:fully |100% )?remote|work from home|wfh"
    r"|telecommut(?:e|ing))\b", re.I)
# "remote work stipend" is a perk of a job, not where the job is done.
_REMOTE_PERKS = re.compile(
    r"\bremote[- ](?:work(?:ing)?\s+)?(?:stipend|allowance|policy|policies|equipment|set-?up|tools|culture|benefits?|perks?|reimbursement)\b"
    r"|\bwork[- ]from[- ]home\s+(?:stipend|allowance|equipment|set-?up|reimbursement)\b", re.I)
# "Remote" as part of a job's subject ("remote sensing"), not its location.
_REMOTE_SUBJECT = re.compile(
    r"\bremote[- ](?:sensing|sensors?|controls?|controlled|operated|operations?|monitoring|desktop|access|areas?|communities|villages?)\b", re.I)
_NEGATED_REMOTE = re.compile(
    r"\b(?:not|isn'?t|aren'?t|cannot be|can'?t be|non)[- ](?:(?:a|an|the|fully|100%|completely|entirely|eligible for)\s+)*remote\b"
    r"|\bno (?:remote|work[- ]from[- ]home|telecommut\w*)\b|\bremote (?:work |working )?(?:is|are) (?:not|unavailable)\b"
    r"|\b(?:does|do|will) not (?:offer|allow|support|permit) (?:remote|work[- ]from[- ]home|telecommut\w*)\b"
    r"|\bwithout remote\b", re.I)
# In-person interviews, events and occasional travel do not make a job on-site.
_ONSITE_OCCASIONS = re.compile(
    r"\b(?:in[- ]person|on[- ]?site)\s+(?:interviews?|meetings?|events?|training|orientation|onboarding|assessments?|rounds?|retreats?|offsites?|team\s+\w+)\b"
    r"|\binterviews?\s+(?:will be\s+|are\s+)?(?:conducted\s+|held\s+)?(?:in[- ]person|on[- ]?site)\b"
    r"|\b(?:occasional(?:ly)?|periodic(?:ally)?|quarterly|annual(?:ly)?|monthly)\s+(?:in[- ]person|on[- ]?site|travel)\b", re.I)


def clean_arrangement_text(text):
    """The text with perks, subjects, interview wording and negated 'remote' taken out, and whether remote was negated."""
    text = str(text or "")
    for pattern in (_REMOTE_PERKS, _REMOTE_SUBJECT, _ONSITE_OCCASIONS):
        text = pattern.sub(" ", text)
    negated = bool(_NEGATED_REMOTE.search(text))
    return _NEGATED_REMOTE.sub(" NOTREMOTE ", text), negated


def arrangement_types(text):
    """Which of Remote, Hybrid and Onsite the text states outright."""
    text, negated = clean_arrangement_text(text)
    text = _HYBRID_WORDS.sub("HYBRID", text)
    found = set()
    for name, pattern in (("Hybrid", _HYBRID_WORDS), ("Remote", _REMOTE_WORDS), ("Onsite", _ONSITE_WORDS)):
        if pattern.search(text):
            found.add(name)
    if negated and "Hybrid" not in found:
        found.discard("Remote")
        found.add("Onsite")  # "not remote" means the work is done in person
    return found


def posting_arrangement(title, location, location_type, description, remote_flag):
    """Hybrid, Remote, Onsite or None for a JobPosting."""
    description = str(description or "")[:2500]
    description_text, description_negated = clean_arrangement_text(description)
    title_and_place = arrangement_types(f"{title} {location}")
    if _HYBRID_WORDS.search(f"{location_type} {title} {location} {description}"):
        return "Hybrid"
    if remote_flag or "Remote" in title_and_place or _DESCRIBED_REMOTE.search(description_text):
        return "Remote"
    if "Onsite" in title_and_place or description_negated or _ONSITE_WORDS.search(description_text):
        return "Onsite"
    return None


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


def has_job_posting(soup):
    """True when the page carries a JobPosting record (schema.org structured data)."""
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or script.get_text())
        except (ValueError, TypeError):
            continue
        if any(_is_type(node, "JobPosting") for node in _nodes(data)):
            return True
    return False


def is_article_page(soup):
    """A page marked as an article, unless it also holds a real job posting.

    Some career sites (Home Depot's, for one) mark every job page og:type=article.
    """
    return bool(soup.find("meta", attrs={"property": "og:type", "content": "article"})) and not has_job_posting(soup)


def extract_jobs(url, html, wanted):
    """Only return individual postings with a matching title and direct URL."""
    soup = BeautifulSoup(html, "html.parser")
    if is_article_page(soup):
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
            if re.search(r"\bremote\b", clean_arrangement_text(location)[0], re.I):
                remote = True
            employer = _plain(node.get("hiringOrganization"))
            description = BeautifulSoup(_plain(node.get("description")), "html.parser").get_text(" ", strip=True)
            location_type = _plain(node.get("jobLocationType"))
            arrangement = posting_arrangement(title, location, location_type, description, remote)
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
        if NON_JOB_PATH.search(path) or is_pdf_url(href):
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
        if (target and urlparse(target).hostname == origin and target != canonical_url(url)
                and not is_pdf_url(target)):
            result.append(target)
    return list(dict.fromkeys(result))[:limit]
