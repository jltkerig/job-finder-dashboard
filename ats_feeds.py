"""Discover public postings from employer linked Lever and Greenhouse boards."""
from urllib.parse import urlparse

import requests
from job_listings import matching_title


def public_board_links(board_url, wanted, timeout=10):
    parsed = urlparse(board_url or "")
    if parsed.scheme != "https":
        return []
    host = parsed.hostname or ""
    parts = [piece for piece in parsed.path.split("/") if piece]
    if len(parts) != 1:
        return []
    site = parts[0]
    if not site.replace("-", "").replace("_", "").isalnum():
        return []
    if host in {"jobs.lever.co", "jobs.eu.lever.co"}:
        api_host = "api.eu.lever.co" if host == "jobs.eu.lever.co" else "api.lever.co"
        api_url = f"https://{api_host}/v0/postings/{site}"
        params = {"mode": "json", "limit": 100}
    elif host in {"boards.greenhouse.io", "job-boards.greenhouse.io"}:
        api_url = f"https://boards-api.greenhouse.io/v1/boards/{site}/jobs"
        params = {}
    else:
        return []
    try:
        response = requests.get(api_url, params=params, timeout=timeout,
                                headers={"User-Agent": "PersonalJobFinder/1.1"})
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError):
        return []
    jobs = payload.get("jobs", []) if isinstance(payload, dict) else payload
    if not isinstance(jobs, list):
        return []
    urls = []
    for item in jobs[:100]:
        if not isinstance(item, dict):
            continue
        if not matching_title(item.get("title") or item.get("text"), wanted):
            continue
        link = item.get("hostedUrl") or item.get("absolute_url")
        if isinstance(link, str) and link.startswith("https://"):
            urls.append(link)
    return urls
