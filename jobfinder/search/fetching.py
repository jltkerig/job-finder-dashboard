"""Downloading pages politely: which domains are allowed, the wait between requests to one site, and a cache of pages already read."""

from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse
import requests
import time

from jobfinder.search import docker
from jobfinder.search import shared
from jobfinder.search.shared import BLOCKED_COUNTRY_DOMAINS, BLOCKED_DOMAINS, HEADERS, MAX_HTML_SIZE, PREFETCH_WORKERS, TIMEOUT, USA_ONLY, _host_lock, _host_next_request, _page_cache, _prefetched, timed
from jobfinder.sources.job_listings import canonical_url, is_pdf_url


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


def wait_for_host(url):
    """Space out requests to one site by shared.REQUEST_DELAY; requests to different sites do not wait for each other."""
    host = (urlparse(url).netloc or "").casefold()
    with _host_lock:
        now = time.monotonic()
        start = max(now, _host_next_request.get(host, 0.0))
        _host_next_request[host] = start + shared.REQUEST_DELAY
    if start > now:
        time.sleep(start - now)


def remember_page(key, response):
    """Keep a fetched page (or a failed fetch) for this search; the oldest entry goes when the cache is full."""
    if shared.update_existing_mode:
        return
    with _host_lock:
        if key not in _page_cache and len(_page_cache) >= 250:
            _page_cache.pop(next(iter(_page_cache)))
        _page_cache[key] = response


def prefetch_pages(urls, workers=None):
    """Download several pages at once so the checks that follow find them in the cache."""
    if shared.update_existing_mode:
        return
    todo = [url for url in dict.fromkeys(urls) if url and is_valid_url(url) and not is_pdf_url(url)
            and canonical_url(url) not in _page_cache]
    if len(todo) < 2:
        return

    def fetch(url):
        try:
            response = safe_request(url)
            if response is None and docker.check_searxng_timer():
                remember_page(canonical_url(url), None)
        except Exception:
            pass

    with ThreadPoolExecutor(max_workers=workers or PREFETCH_WORKERS) as pool:
        list(pool.map(fetch, todo))


def safe_request(url):
    with timed("Page downloads (added up across parallel downloads)"):
        return _safe_request(url)


def _safe_request(url):
    if not shared.update_existing_mode and not docker.check_searxng_timer():
        return None

    if not is_valid_url(url) or is_pdf_url(url):
        return None

    key = canonical_url(url)
    if not shared.update_existing_mode and key in _page_cache:
        return _page_cache[key]
    if shared.update_existing_mode:
        # A page fetched a moment ago for this same row is fresh enough; each is handed out only once.
        with _host_lock:
            ready = _prefetched.pop(key, None)
        if ready is not None:
            response, failure_status = ready
            if response is None:
                shared._last_failure_status = failure_status
            return response
    return _fetch_page(url, key)


def prefetch_for_update(urls):
    """Download the pages the next rows will need, several at a time (Update and Refresh only)."""
    todo = [url for url in dict.fromkeys(urls) if url and is_valid_url(url) and not is_pdf_url(url)
            and canonical_url(url) not in _prefetched]
    if len(todo) < 2:
        return

    def fetch(url):
        key = canonical_url(url)
        failure = []
        try:
            response = _fetch_page(url, key, failure)
        except Exception:
            response = None
        with _host_lock:
            _prefetched[key] = (response, failure[0] if failure else None)

    with timed("Page downloads (added up across parallel downloads)"):
        with ThreadPoolExecutor(max_workers=PREFETCH_WORKERS) as pool:
            list(pool.map(fetch, todo))


def _fetch_page(url, key, failure=None):
    """Download one page. A failed download's HTTP status goes into `failure` (a list), or shared._last_failure_status."""
    try:
        wait_for_host(url)

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

        remember_page(key, response)

        return response

    except requests.RequestException as error:
        # Lets callers tell a removed page (404/410) from a temporary failure.
        code = getattr(error.response, "status_code", None)
        if failure is not None:
            failure.append(code)
        else:
            shared._last_failure_status = code
        return None
