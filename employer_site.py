"""Find an employer's own website and careers page from a job-board listing."""
import json
import re
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from job_listings import canonical_url, extract_jobs, is_pdf_url, job_links

_LEGAL_WORDS = {"inc", "llc", "ltd", "co", "corp", "corporation", "company", "the", "group",
                "holdings", "limited", "lp", "llp", "pllc", "incorporated"}
# Names like these describe a department, not a website, so a domain guess would be noise.
_GENERIC_NAME = re.compile(r"\b(?:office|department|dept|division|bureau|agency|county|city|state|"
                           r"university|college|school|human resources|hr|careers|jobs)\b", re.I)
GUESS_TLDS = ("com", "org")
_SCHOOL_WORDS = {"university", "college", "institute", "polytechnic", "academy", "school"}
_FILLER_WORDS = {"of", "the", "at", "and", "for"}
CAREER_HOST_RE = re.compile(r"^(?:jobs|careers?|employment|hr|join|apply)\.", re.I)
CAREER_PATHS = ("/careers/", "/careers", "/jobs/", "/jobs", "/join-us/", "/work-with-us/",
                "/about/careers/", "/company/careers/", "/join-our-team/")
CAREER_PATH_RE = re.compile(r"/(?:careers?|jobs?|join[a-z-]*|work-with-us|employment)(?:/|$)", re.I)
CAREER_MARKER_RE = re.compile(r"career|\bjobs?\b|join (?:us|our team)|work with us|employment|hiring", re.I)
NOT_FOUND_RE = re.compile(r"\b(?:404|page not found|not found)\b", re.I)
WEBSITE_LINK_TERMS = ("website", "visit website", "company website", "official site")
MAX_CAREER_FETCHES = 5
MAX_POSTING_FETCHES = 3
_cache = {}


def clear_cache():
    _sitemap_cache.clear()
    _cache.clear()


def name_tokens(name):
    words = re.findall(r"[a-z0-9]+", str(name or "").casefold())
    return [word for word in words if word not in _LEGAL_WORDS]


def name_slug(name):
    return "".join(name_tokens(name))


def bare_host(url):
    return (urlparse(str(url or "")).hostname or "").casefold().removeprefix("www.")


def host_root(host):
    labels = [part for part in str(host or "").casefold().removeprefix("www.").split(".") if part]
    if len(labels) >= 3 and len(labels[-1]) == 2 and labels[-2] in {"co", "com", "org", "gov", "edu", "ac", "net"}:
        return labels[-3]
    return labels[-2] if len(labels) >= 2 else (labels[0] if labels else "")


def host_matches_company(host, company):
    slug = name_slug(company)
    root = host_root(host)
    tokens = name_tokens(company)
    if not slug or not root:
        return False
    return (root == slug or (len(slug) >= 4 and slug in root) or (len(root) >= 5 and root in slug)
            or (len(tokens[0]) >= 4 and tokens[0] == root))


def school_slugs(company):
    """Likely domain names for a school: its initials (jhu) and its name without 'University' (johnshopkins)."""
    tokens = [t for t in re.findall(r"[a-z0-9]+", str(company or "").casefold())
              if t not in _LEGAL_WORDS and t not in _FILLER_WORDS]
    if not tokens or not (set(tokens) & _SCHOOL_WORDS):
        return []
    core = [t for t in tokens if t not in _SCHOOL_WORDS]
    slugs = []
    if len(tokens) >= 3:
        slugs.append("".join(t[0] for t in tokens))
    if core:
        slugs.append("".join(core))
    return [slug for slug in dict.fromkeys(slugs) if len(slug) >= 3]


def same_site(host, base):
    """True when host is the base domain or one of its subdomains (jobs.jhu.edu under jhu.edu)."""
    host, base = str(host or "").removeprefix("www."), str(base or "").removeprefix("www.")
    return bool(host and base) and (host == base or host.endswith("." + base))


def is_third_party(posting_url, company):
    """True when the listing lives on a site that is not the employer's own."""
    return not host_matches_company(bare_host(posting_url), company)


def can_guess_domain(company):
    return len(name_slug(company)) >= 4 and not _GENERIC_NAME.search(str(company or ""))


_GENERIC_TAIL = {"technologies", "technology", "tech", "solutions", "systems", "services", "labs", "lab", "studio",
                 "studios", "software", "digital", "media", "consulting", "partners", "group", "design", "designs",
                 "agency", "associates", "international", "global", "worldwide", "supplies", "supply", "products",
                 "enterprises", "industries", "goods", "brands", "wholesale", "distribution", "distributors",
                 "company", "co", "corp", "corporation", "holdings", "usa"}


def _names_match(company, text):
    """The company's name must appear as whole words ('Wise' is not in 'Otherwise Solutions')."""
    tokens = name_tokens(company)
    while len(tokens) > 1 and tokens[-1] in _GENERIC_TAIL:
        tokens = tokens[:-1]
    if not tokens:
        return False
    words = re.findall(r"[a-z0-9]+", str(text or "").casefold())
    size = len(tokens)
    return any(words[i:i + size] == tokens for i in range(len(words) - size + 1))


def _walk(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            if isinstance(child, (dict, list)):
                yield from _walk(child)
    elif isinstance(value, list):
        for item in value:
            yield from _walk(item)


def _ld_nodes(soup):
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or script.get_text())
        except (ValueError, TypeError):
            continue
        yield from _walk(data)


def _has_type(node, kinds):
    types = node.get("@type") or []
    types = [types] if isinstance(types, str) else types
    return any(str(kind).split("/")[-1].casefold() in kinds for kind in types)


def _page_names(soup):
    names = []
    meta = soup.find("meta", attrs={"property": "og:site_name"})
    if meta and meta.get("content"):
        names.append(meta["content"])
    if soup.title and soup.title.string:
        names.append(soup.title.string)
    heading = soup.find("h1")
    if heading:
        names.append(heading.get_text(" ", strip=True))
    for node in _ld_nodes(soup):
        if _has_type(node, {"organization", "corporation", "localbusiness", "website", "professionalservice"}):
            names.append(str(node.get("name") or ""))
    return names


def _candidates(company, posting_url, posting_html, *, search, location_hint, allow_search, is_excluded):
    """Yield (homepage url, how it was found, trusted). Trusted ones came from the listing itself."""
    posting_host = bare_host(posting_url)

    def usable(url):
        host = bare_host(url)
        return (str(url).startswith(("http://", "https://")) and bool(host)
                and host != posting_host and not is_excluded(host))

    soup = BeautifulSoup(posting_html or "", "html.parser")
    for node in _ld_nodes(soup):
        organization = node.get("hiringOrganization") if _has_type(node, {"jobposting"}) else None
        if isinstance(organization, dict):
            for key in ("url", "sameAs"):
                values = organization.get(key)
                for value in (values if isinstance(values, list) else [values]):
                    if isinstance(value, str) and usable(value):
                        yield value, "listing data (hiringOrganization)", True
    for link in soup.find_all("a", href=True):
        text = link.get_text(" ", strip=True).casefold()
        if any(term in text for term in WEBSITE_LINK_TERMS):
            href = urljoin(posting_url, link["href"])
            if usable(href):
                yield href, "company website link on the listing", True
    for slug in school_slugs(company):
        for prefix in ("", "www."):
            url = f"https://{prefix}{slug}.edu/"
            if usable(url):
                yield url, "school domain guess", False
    if can_guess_domain(company):
        # "Szco Supplies Inc" -> szcosupplies, then szco (without its generic ending words).
        slugs = [name_slug(company)]
        tokens = name_tokens(company)
        while len(tokens) > 1 and tokens[-1] in _GENERIC_TAIL:
            tokens = tokens[:-1]
            trimmed = "".join(tokens)
            if len(trimmed) >= 4 and trimmed not in slugs:
                slugs.append(trimmed)
        for slug in slugs:
            for tld in GUESS_TLDS:
                for prefix in ("", "www."):
                    url = f"https://{prefix}{slug}.{tld}/"
                    if usable(url):
                        yield url, "domain guess", False
    if allow_search and search:
        seen = set()
        for result in (search(f'"{company}" {location_hint}'.strip()) or [])[:8]:
            url = str(result.get("url") or "")
            host = bare_host(url)
            if usable(url) and host not in seen:
                seen.add(host)
                parsed = urlparse(url)
                yield f"{parsed.scheme}://{parsed.netloc}/", "web search", False


def _find_site(company, posting_url, posting_html, *, fetch, search, is_excluded, location_hint,
               allow_search, notes):
    fetched = set()
    for url, method, trusted in _candidates(company, posting_url, posting_html, search=search,
                                            location_hint=location_hint, allow_search=allow_search,
                                            is_excluded=is_excluded):
        host = bare_host(url)
        if host in fetched:
            continue
        response = fetch(url)
        if response is None:
            continue
        fetched.add(host)
        final_host = bare_host(getattr(response, "url", "") or url)
        if not final_host or is_excluded(final_host) or final_host == bare_host(posting_url):
            continue
        soup = BeautifulSoup(response.text or "", "html.parser")
        if not trusted and not any(_names_match(company, name) for name in _page_names(soup)):
            notes.append(f"{host} ({method}) rejected: its page does not name {company}")
            continue
        return response, method
    return None


def _find_careers(home, *, fetch, score_page):
    base_url = getattr(home, "url", "") or ""
    parsed = urlparse(base_url)
    origin = f"{parsed.scheme}://{parsed.netloc}/"
    host = bare_host(base_url)
    soup = BeautifulSoup(home.text or "", "html.parser")
    linked = []
    for link in soup.find_all("a", href=True):
        if CAREER_MARKER_RE.search(f"{link.get_text(' ', strip=True)} {link['href']}"):
            target = urljoin(base_url, link["href"]).split("#")[0]
            if target.startswith(("http://", "https://")) and same_site(bare_host(target), host) and not is_pdf_url(target):
                linked.append(target)
    seen, ranked, fetches = set(), [], 0
    for target in linked[:3] + [urljoin(origin, path) for path in CAREER_PATHS]:
        key = canonical_url(target)
        if key in seen:
            continue
        seen.add(key)
        if fetches >= MAX_CAREER_FETCHES:
            break
        fetches += 1
        page = fetch(target)
        if page is None or not same_site(bare_host(getattr(page, "url", "") or target), host):
            continue
        page_soup = BeautifulSoup(page.text or "", "html.parser")
        heading = page_soup.find("h1")
        label = f"{page_soup.title.string if page_soup.title and page_soup.title.string else ''} " \
                f"{heading.get_text(' ', strip=True) if heading else ''}"
        if NOT_FOUND_RE.search(label):
            continue
        page_url = getattr(page, "url", "") or target
        path = urlparse(page_url).path or "/"
        careers_like = bool(CAREER_PATH_RE.search(path) or CAREER_HOST_RE.match(bare_host(page_url)))
        score = int((score_page(page_url, page.text) or {}).get("score", 0)) if score_page else 0
        if not (careers_like or score >= 3):
            continue
        ranked.append(((careers_like, score, -len(path)), page))
        if careers_like and re.fullmatch(r"/(?:careers?|jobs?)/?", path, re.I):
            break
    return max(ranked, key=lambda item: item[0])[1] if ranked else None


def _find_posting(careers_url, job_title, titles, *, fetch):
    """The exact opening on the employer's site, if its careers page (or pages it links to) lists it."""
    page = fetch(careers_url)
    if page is None:
        return None
    wanted = [job_title] if job_title else list(titles)
    for opening in extract_jobs(page.url, page.text, wanted):
        if opening.get("url"):
            return opening["url"]
    host = bare_host(careers_url)
    fetched = 0
    for link in job_links(page.url, page.text, limit=8):
        if fetched >= MAX_POSTING_FETCHES:
            break
        if not same_site(bare_host(link), host):
            continue
        fetched += 1
        detail = fetch(link)
        if detail is None:
            continue
        for opening in extract_jobs(detail.url, detail.text, wanted):
            if opening.get("url"):
                return opening["url"]
    return None


_JOB_PATH = re.compile(r"job|career|position|opening|vacanc|employment|hiring|apply|join|work-with", re.I)
_TITLE_FILLER = {"and", "the", "for", "of", "an", "in", "at", "to", "with", "senior", "sr", "jr", "junior", "lead", "ii", "iii"}
_sitemap_cache = {}
MAX_SITEMAP_FILES = 8


def _sitemap_urls(origin, fetch_raw):
    """Page addresses listed in a site's sitemap files (robots.txt and the usual names), cached per site."""
    if origin in _sitemap_cache:
        return _sitemap_cache[origin]
    queue = []
    robots = fetch_raw(origin + "/robots.txt")
    if robots is not None:
        queue += [line.split(":", 1)[1].strip() for line in robots.text.splitlines() if line.lower().startswith("sitemap:")]
    queue += [origin + path for path in ("/sitemap.xml", "/sitemap_index.xml", "/wp-sitemap.xml", "/job-sitemap.xml")]
    urls, seen, files = [], set(), 0
    while queue and files < MAX_SITEMAP_FILES:
        address = queue.pop(0)
        if address in seen:
            continue
        seen.add(address)
        response = fetch_raw(address)
        files += 1
        if response is None:
            continue
        nested = []
        for loc in re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", response.text, flags=re.I)[:8000]:
            last = loc.rsplit("/", 1)[-1].lower()
            if last.endswith(".xml") or last.endswith(".xml.gz") or "sitemap" in last:
                nested.append(loc)
            else:
                urls.append(loc)
        # Sitemap files about jobs or careers are read first.
        queue = sorted(nested, key=lambda loc: 0 if _JOB_PATH.search(loc) else 1) + queue
    _sitemap_cache[origin] = urls
    return urls


def _find_in_sitemap(domain, job_title, *, fetch, fetch_raw):
    """The page for this opening, found in the employer's sitemap and checked against the page itself."""
    words = [word for word in re.findall(r"[a-z0-9]+", str(job_title or "").casefold()) if word not in _TITLE_FILLER and len(word) > 1]
    if not domain or not words:
        return None
    candidates = []
    for url in _sitemap_urls(f"https://{domain}", fetch_raw):
        path = urlparse(url).path
        if not _JOB_PATH.search(path):
            continue
        path_words = set(re.findall(r"[a-z0-9]+", path.casefold()))
        if all(word in path_words for word in words):
            candidates.append((len(path_words), url))
    for _, url in sorted(candidates)[:3]:
        page = fetch(url)
        if page is None:
            continue
        soup = BeautifulSoup(page.text, "html.parser")
        heading = " ".join(tag.get_text(" ", strip=True) for tag in soup.select("title, h1")).casefold()
        if all(word in re.findall(r"[a-z0-9]+", heading) for word in words):
            return getattr(page, "url", url) or url
    return None


def resolve_employer_site(company, job_title, posting_url, posting_html, titles, *, fetch, search=None,
                          score_page=None, is_excluded=None, location_hint="", allow_search=True, notes=None,
                          fetch_raw=None):
    """Return {domain, careers_url, posting_url, method, evidence} for the employer's own site, or None.

    fetch(url) returns a response with .url and .text, or None. search(query) returns SearXNG-style
    results. Nothing here writes to the database; callers decide what to store.
    """
    notes = notes if notes is not None else []
    company = " ".join(str(company or "").split())
    if not company or not posting_url:
        return None
    if not is_third_party(posting_url, company):
        notes.append("listing is already on the employer's own domain")
        return None
    is_excluded = is_excluded or (lambda host: False)
    use_search = bool(allow_search and search)
    key = (name_slug(company) or company.casefold(), use_search)
    if key not in _cache:
        site = None
        found = _find_site(company, posting_url, posting_html, fetch=fetch, search=search if use_search else None,
                           is_excluded=is_excluded, location_hint=location_hint, allow_search=use_search,
                           notes=notes)
        if found:
            home, method = found
            careers = _find_careers(home, fetch=fetch, score_page=score_page)
            if careers:
                careers_url = getattr(careers, "url", "")
                site = {"domain": bare_host(getattr(home, "url", "")), "careers_url": careers_url, "method": method,
                        "evidence": [f"employer site: {bare_host(getattr(home, 'url', ''))} ({method})",
                                     "careers page found on the employer's site"]}
            else:
                host = bare_host(getattr(home, "url", ""))
                notes.append(f"{host} found ({method}) but it has no careers page")
                # The employer is still identified; the listing stays the way to apply.
                site = {"domain": host, "careers_url": None, "method": method,
                        "evidence": [f"employer site: {host} ({method})",
                                     "no careers page on the employer's site; apply through the listing"]}
        else:
            notes.append("no employer website could be verified")
        _cache[key] = site
    site = _cache[key]
    if site is None:
        return None
    result = dict(site, evidence=list(site["evidence"]))
    # A listing on a subdomain of the employer's website (corcoran.gwu.edu under gwu.edu) is on the employer's own site:
    # that subdomain is the website to show, and the listing is the posting.
    listing_host = bare_host(posting_url)
    if site["domain"] and listing_host != site["domain"] and listing_host.endswith("." + site["domain"]):
        result["domain"] = listing_host
        result["posting_url"] = posting_url
        result["evidence"].append(f"this listing is on the employer's own site ({listing_host})")
        return result
    result["posting_url"] = _find_posting(site["careers_url"], job_title, titles, fetch=fetch) if site["careers_url"] else None
    if result["posting_url"]:
        result["evidence"].append("this opening found on the employer's site")
    elif fetch_raw is not None:
        # Not on the careers page: the sitemap lists pages no search ranks and no page links to.
        result["posting_url"] = _find_in_sitemap(site["domain"], job_title, fetch=fetch, fetch_raw=fetch_raw)
        if result["posting_url"]:
            result["evidence"].append("this opening found in the employer's sitemap")
    return result
