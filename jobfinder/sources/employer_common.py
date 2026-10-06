"""Shared pieces for the employer career-site adapters: polite HTTP, text cleanup and the opening record."""

import html as html_lib
import json
import os
import re
import time
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from jobfinder import paths
from jobfinder.sources.job_listings import extract_jobs, matching_title
from jobfinder.sources.remote_states import is_remote_place, place_states

USER_AGENT = "Mozilla/5.0 (compatible; PersonalJobFinder/1.1; local job search)"
MAX_PAGES = 3  # result pages read per keyword on paged boards (tests may lower it)
HTML = "text/html,application/xhtml+xml"


class Http:
    """requests with a user agent, a timeout, and a short pause between calls."""

    def __init__(self, delay=0.4, timeout=30):
        self.delay, self.timeout, self._last = delay, timeout, 0.0

    def _wait(self):
        pause = self.delay - (time.monotonic() - self._last)
        if pause > 0:
            time.sleep(pause)
        self._last = time.monotonic()

    def get(self, url, params=None, accept="application/json", headers=None):
        self._wait()
        return requests.get(url, params=params, headers=dict({"User-Agent": USER_AGENT, "Accept": accept}, **(headers or {})),
                            timeout=self.timeout)

    def post_json(self, url, payload):
        self._wait()
        return requests.post(url, json=payload, timeout=self.timeout, headers={
            "User-Agent": USER_AGENT, "Accept": "application/json", "Content-Type": "application/json"})


def _plain(html):
    return BeautifulSoup(html_lib.unescape(str(html or "")), "html.parser").get_text(" ", strip=True)


def _arrangement(text):
    text = str(text or "").casefold().replace("_", " ")
    if "hybrid" in text:
        return "Hybrid"
    if "remote" in text or "virtual" in text or "telecommut" in text:
        return "Remote"
    if re.search(r"on[- ]?site|in[- ]?office|in[- ]?person", text):
        return "Onsite"
    return None


def _schedule(text):
    text = str(text or "").casefold()
    return "FULL_TIME" if "full" in text else "PART_TIME" if "part" in text else ""


def _opening(employer, *, title, url, locations, html, posted="", schedule="", arrangement=None, country="",
             remote_states=(), category=""):
    locations = [place for place in dict.fromkeys(locations) if place]
    # "Work At Home-Massachusetts" style places make a job remote and name the states it is open to.
    remote_states = set(remote_states) | place_states(locations)
    if arrangement is None and locations and all(is_remote_place(place) for place in locations):
        arrangement = "Remote"
    return {"title": title[:255], "url": url, "company": employer.name, "location": locations[0] if locations else "",
            "locations": locations, "type": arrangement, "schedule": schedule, "salary": "", "posted": posted or None,
            "evidence": [f"{employer.name} careers site ({employer.system.title()})", "title matches search"],
            "description": _plain(html), "html": str(html or ""), "country": country,
            "remote_states": remote_states, "category": category}




class _WholeBoard:
    """Boards that publish every opening in one request: read once per run, then title matching picks."""

    def __init__(self, config):
        self.config = config
        self._all = None

    def search(self, keyword, http):
        if self._all is None:
            self._all = [self._listing(job) for job in self._fetch(http)]
        return self._all

    def _listing(self, job):
        return {"title": job.get("title") or "", "id": str(job.get("id")), "path": str(job.get("id")),
                "location": self._first_place(job), "raw": job}

    def status(self, url, http):
        wanted = self._id_from(url)
        if not wanted:
            return "Unknown"
        return "Open" if any(str(job.get("id")) == wanted for job in self._fetch(http)) else "Closed"

    def owns(self, url):
        return False


def _get_json(http, url, params=None):
    response = http.get(url, params=params)
    if response.status_code != 200:
        raise ValueError(f"{urlparse(url).hostname} answered HTTP {response.status_code}")
    return response.json()

