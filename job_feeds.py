"""Public remote-job feeds (Remote OK, Remotive, We Work Remotely) in one shape.

Each provider's own listing URL stays the link and the provider is named on the listing, as their
terms ask. Remotive asks for at most about 4 requests a day, so its data is kept on disk for 24 hours.
"""
import json
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

import remote_ok
from job_listings import matching_title

USER_AGENT = "PersonalJobFinder/1.1 (local job search)"
CACHE_DIR = Path(__file__).resolve().parent / "feed_cache"

# Four categories = four requests per day at most, inside Remotive's advice of about 4 a day.
REMOTIVE_CATEGORIES = ("design", "software-development", "marketing", "writing")
REMOTIVE_CACHE_SECONDS = 24 * 3600
WWR_FEEDS = ("remote-design-jobs", "remote-front-end-programming-jobs", "remote-full-stack-programming-jobs",
             "all-other-remote-jobs")
WWR_CACHE_SECONDS = 6 * 3600


def _cached(name, max_age, load):
    """Return load()'s result, reusing a saved copy younger than max_age seconds (or any copy if load() fails)."""
    path = CACHE_DIR / f"{name}.json"
    stored = None
    try:
        stored = json.loads(path.read_text(encoding="utf-8"))
        if time.time() - float(stored["fetched_at"]) < max_age:
            return stored["data"]
    except (OSError, ValueError, KeyError, TypeError):
        stored = None
    try:
        data = load()
    except (requests.RequestException, ValueError):
        if stored is not None:
            return stored["data"]
        raise
    try:
        CACHE_DIR.mkdir(exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps({"fetched_at": time.time(), "data": data}), encoding="utf-8")
        temporary.replace(path)
    except OSError:
        pass
    return data


def _date(value):
    try:
        parsed = parsedate_to_datetime(value) if value else None
    except (TypeError, ValueError):
        parsed = None
    if parsed is None and value:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed is not None and parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def fetch_remotive(timeout=20):
    jobs, seen, failures = [], set(), 0
    for category in REMOTIVE_CATEGORIES:
        def load(category=category):
            response = requests.get("https://remotive.com/api/remote-jobs", params={"category": category},
                                    headers={"User-Agent": USER_AGENT}, timeout=timeout)
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise ValueError("Remotive sent an unexpected response")
            return payload.get("jobs", [])
        try:
            items = _cached(f"remotive-{category}", REMOTIVE_CACHE_SECONDS, load)
        except (requests.RequestException, ValueError):
            failures += 1
            continue
        for item in items:
            url = item.get("url") if isinstance(item, dict) else None
            if not url or url in seen:
                continue
            seen.add(url)
            jobs.append({"id": str(item.get("id") or url), "position": str(item.get("title") or ""),
                         "company": str(item.get("company_name") or ""),
                         "location": str(item.get("candidate_required_location") or ""), "url": url,
                         "description": str(item.get("description") or "")[:200000],
                         "salary": str(item.get("salary") or ""), "posted": str(item.get("publication_date") or "")[:10]})
    if failures == len(REMOTIVE_CATEGORIES):
        raise ValueError("Remotive is unavailable")
    return jobs


def parse_we_work_remotely(content):
    """Jobs from one We Work Remotely RSS feed; expired postings are dropped."""
    try:
        root = ET.fromstring(content)
    except ET.ParseError as error:
        raise ValueError(f"We Work Remotely feed could not be read: {error}")
    now = datetime.now(timezone.utc)
    items = []
    for node in root.iter("item"):
        def text(tag):
            child = node.find(tag)
            return (child.text or "").strip() if child is not None else ""
        title = text("title")
        link = text("link") or text("guid")
        if not title or not link:
            continue
        company, separator, position = title.partition(": ")
        if not separator:
            company, position = "", title
        expires = _date(text("expires_at"))
        if expires is not None and expires < now:
            continue
        posted = _date(text("pubDate"))
        items.append({"id": text("guid") or link, "position": position.strip(), "company": company.strip(),
                      "location": text("region"), "url": link, "description": text("description"),
                      "posted": posted.date().isoformat() if posted else "", "type": text("type")})
    return items


def fetch_we_work_remotely(timeout=20):
    jobs, seen, failures = [], set(), 0
    for slug in WWR_FEEDS:
        def load(slug=slug):
            response = requests.get(f"https://weworkremotely.com/categories/{slug}.rss",
                                    headers={"User-Agent": USER_AGENT}, timeout=timeout)
            response.raise_for_status()
            return parse_we_work_remotely(response.content)
        try:
            items = _cached(f"wwr-{slug}", WWR_CACHE_SECONDS, load)
        except (requests.RequestException, ValueError):
            failures += 1
            continue
        for item in items:
            if item["url"] not in seen:
                seen.add(item["url"])
                jobs.append(item)
    if failures == len(WWR_FEEDS):
        raise ValueError("We Work Remotely is unavailable")
    return jobs


def _matching(feed, jobs, titles, usa_only=True):
    """Listings with a matching title, open to U.S. applicants, and linked on the provider's own site."""
    for job in jobs:
        position = str(job.get("position") or "")
        if not matching_title(position, titles):
            continue
        location = str(job.get("location") or "")
        explicit_us = bool(re.search(r"\b(?:USA?|United States)\b", location, re.I))
        eligible = explicit_us or bool(re.search(r"\b(?:Anywhere|Worldwide|Global|World|Americas?|North(?:ern)? America)\b",
                                                 location, re.I))
        if usa_only and not eligible:
            continue
        url = str(job.get("url") or "")
        parsed = urlparse(url)
        if parsed.scheme != "https" or parsed.hostname not in feed.hosts:
            continue
        description = str(job.get("description") or "")[:200000]
        text = BeautifulSoup(description, "html.parser").get_text(" ", strip=True)
        yield {"name": str(job.get("company") or "").strip()[:255], "title": position[:255], "url": url,
               "location": location, "html": description, "text": text, "posted": job.get("posted"),
               "usa_score": 6 if explicit_us else 5 if eligible else 0}


class Feed:
    def __init__(self, name, domain, fetch, hosts, matcher=None):
        self.name, self.domain, self.fetch, self.hosts = name, domain, fetch, set(hosts)
        self._matcher = matcher

    def matching(self, jobs, titles, usa_only=True):
        if self._matcher is not None:
            return self._matcher(jobs, titles, usa_only)
        return _matching(self, jobs, titles, usa_only)

    def index(self, jobs):
        """Listing URL -> listing, for checking whether a saved job is still in the feed."""
        return {str(job.get("url") or job.get("apply_url")): job for job in jobs}


FEEDS = (
    Feed("Remote OK", "remoteok.com", remote_ok.fetch_jobs, {"remoteok.com", "www.remoteok.com"},
         matcher=remote_ok.matching_jobs),
    Feed("Remotive", "remotive.com", fetch_remotive, {"remotive.com", "www.remotive.com"}),
    Feed("We Work Remotely", "weworkremotely.com", fetch_we_work_remotely,
         {"weworkremotely.com", "www.weworkremotely.com"}),
)
FEED_NAMES = frozenset(feed.name for feed in FEEDS)
