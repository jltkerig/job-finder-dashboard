"""Read Remote OK's public feed and retain its required source links."""
import re
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

FEED_URL = "https://remoteok.com/api"


def fetch_jobs(timeout=15):
    response = requests.get(FEED_URL, headers={"User-Agent": "PersonalJobFinder/1.1 (local job search)"}, timeout=timeout)
    response.raise_for_status()
    data = response.json()
    return [item for item in data if isinstance(item, dict) and item.get("id") and item.get("position")]


def matching_jobs(jobs, titles, usa_only=True):
    """A conservative title match and U.S.-eligible location filter."""
    words = [set(re.findall(r"[a-z0-9]+", title.casefold())) for title in titles]
    for job in jobs:
        position = str(job.get("position") or "")
        position_words = set(re.findall(r"[a-z0-9]+", position.casefold()))
        if not any(w and w <= position_words for w in words):
            continue
        location = str(job.get("location") or "")
        eligible = bool(re.search(r"\b(?:USA?|United States|Anywhere|Worldwide|Global)\b", location, re.I))
        if usa_only and not eligible:
            continue
        url = str(job.get("url") or job.get("apply_url") or "")
        parsed = urlparse(url)
        if parsed.scheme != "https" or parsed.hostname not in {"remoteok.com", "www.remoteok.com"}:
            continue
        description = str(job.get("description") or "")[:200000]
        text = BeautifulSoup(description, "html.parser").get_text(" ", strip=True)
        yield {
            "name": str(job.get("company") or "").strip()[:255],
            "title": position[:255], "url": url,
            "location": location, "html": description, "text": text,
            "usa_score": 6 if re.search(r"\b(?:USA?|United States)\b", location, re.I) else 5,
        }
