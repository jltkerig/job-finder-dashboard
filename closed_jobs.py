"""Spot closed jobs, including reposts on job boards whose Apply link leads to an expired notice."""
import html as html_lib
import re
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

MAX_HOPS = 5
MAX_TARGETS = 2
CLOSED_URL = re.compile(r"expired|no[-_]?longer|job[-_]?closed|position[-_]?filled|job[-_]?not[-_]?found", re.I)
CLOSED_TEXT = re.compile(
    r"no longer (?:available|accepting|open)"
    r"|(?:job|position|posting|listing|opening|vacancy|role) (?:is|has been|was|has) (?:closed|filled|expired|removed|withdrawn|cancelled|canceled)"
    r"|this (?:job|position|posting) (?:has )?expired|applications? (?:are|is) (?:now )?closed", re.I)
APPLY_LABEL = re.compile(
    r"^\s*apply(?:\s+(?:now|here|online|today|externally|for (?:this )?(?:job|position)"
    r"|on (?:the )?(?:company|employer)(?:'s)? (?:site|website|page)))?\s*[.!]?\s*$", re.I)
NOISE = re.compile(
    r"sentry|google|gstatic|facebook|twitter|doubleclick|cloudflare|schema\.org|w3\.org|amazonaws|jsdelivr|cdn\."
    r"|linkedin\.com/(?:share|sharing)|\.(?:png|jpe?g|gif|svg|webp|css|js|ico|woff2?)(?:\?|$)", re.I)
REDIRECT_HINT = re.compile(r"redirect|apply|outbound|external|goto|click|track|jobid", re.I)


def _clean(url):
    return html_lib.unescape(str(url).replace("\\/", "/").replace("\\u0026", "&"))


def apply_targets(page_url, html, limit=MAX_TARGETS):
    """Addresses the listing's Apply button sends people to, from links or from redirect URLs in the page data."""
    page_host = urlparse(page_url).hostname or ""
    found = []

    def add(candidate):
        if not candidate:
            return
        url = urljoin(page_url, _clean(candidate))
        parsed = urlparse(url)
        if (parsed.scheme in ("http", "https") and parsed.hostname and parsed.hostname != page_host
                and not NOISE.search(url) and url not in found):
            found.append(url)

    soup = BeautifulSoup(html or "", "html.parser")
    for tag in soup.find_all(["a", "button"]):
        if APPLY_LABEL.match(tag.get_text(" ", strip=True)):
            for attribute in ("href", "data-href", "data-url", "data-apply-url", "formaction"):
                add(tag.get(attribute))
    # Buttons often keep their address in script data rather than in a link.
    for url in re.findall(r"https?://[^\s\"'<>\\]+", _clean(html or "")):
        parsed = urlparse(url)
        if REDIRECT_HINT.search(f"{parsed.path}?{parsed.query}"):
            add(url)
    return found[:limit]


def check_apply_target(url, get):
    """Follow redirects one hop at a time. get(url) returns a response (no auto-redirect) or None."""
    current = url
    response = None
    for _ in range(MAX_HOPS + 1):
        response = get(current)
        if response is None:
            return None
        location = (getattr(response, "headers", None) or {}).get("Location")
        if response.status_code in (301, 302, 303, 307, 308) and location:
            current = urljoin(current, location)
            parsed = urlparse(current)
            if CLOSED_URL.search(f"{parsed.path}?{parsed.query}"):
                return {"closed": True, "reason": "its Apply link redirects to an expired-job page", "url": current}
            continue
        break
    else:
        return None
    if response.status_code in (404, 410):
        return {"closed": True, "reason": f"its Apply link leads to HTTP {response.status_code}", "url": current}
    if response.status_code == 200:
        soup = BeautifulSoup(response.text or "", "html.parser")
        title = soup.title.get_text(" ", strip=True) if soup.title else ""
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()
        text = soup.get_text(" ", strip=True)[:1500]
        if CLOSED_TEXT.search(title) or CLOSED_TEXT.search(text) or re.search(r"\bexpired\b", title, re.I):
            return {"closed": True, "reason": "its Apply link leads to a page saying the job is no longer available",
                    "url": current}
    return {"closed": False, "url": current}


def listing_closed(page_url, html, get):
    """A description of why the listing is closed, or None if it looks open or cannot be told."""
    for target in apply_targets(page_url, html):
        result = check_apply_target(target, get)
        if result and result["closed"]:
            return dict(result, apply_link=target)
    return None
