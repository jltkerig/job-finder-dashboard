"""Adapters for boards that publish every opening at once: Greenhouse, Lever, Ashby, BambooHR, Recruitee, Teamtailor,
Rippling."""

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

from jobfinder.sources import employer_common as common
from jobfinder.sources.employer_common import HTML, _WholeBoard, _arrangement, _get_json, _opening, _plain, _schedule  # noqa: F401



class Greenhouse(_WholeBoard):
    def __init__(self, config):
        super().__init__(config)
        self.slug = config["slug"]
        self.base = f"https://boards-api.greenhouse.io/v1/boards/{self.slug}/jobs"

    def _fetch(self, http):
        return _get_json(http, self.base, {"content": "true"}).get("jobs") or []

    def _first_place(self, job):
        return (job.get("location") or {}).get("name", "")

    def _id_from(self, url):
        match = re.search(r"gh_jid=(\d+)|/jobs/(\d+)", url)
        return (match.group(1) or match.group(2)) if match else None

    def detail(self, listing, http, employer):
        job = listing["raw"]
        opening = _opening(employer, title=job.get("title", ""), url=job.get("absolute_url", ""),
                           locations=[self._first_place(job)], html=html_lib.unescape(job.get("content") or ""),
                           posted=str(job.get("first_published") or job.get("updated_at") or "")[:10])
        opening["company"] = job.get("company_name") or employer.name
        return opening


class Lever(_WholeBoard):
    def __init__(self, config):
        super().__init__(config)
        self.slug = config["slug"]
        self.base = f"https://api{'.eu' if config.get('eu') else ''}.lever.co/v0/postings/{self.slug}"

    def _fetch(self, http):
        data = _get_json(http, self.base, {"mode": "json"})
        return [dict(job, title=job.get("text")) for job in data] if isinstance(data, list) else []

    def _first_place(self, job):
        return (job.get("categories") or {}).get("location", "") or ""

    def _id_from(self, url):
        match = re.search(r"/([0-9a-f]{8}-[0-9a-f-]{27})", url)
        return match.group(1) if match else None

    def workplace(self, job):
        """Remote, Hybrid or Onsite as Lever lists the job, or None."""
        return _arrangement(job.get("workplaceType"))

    def detail(self, listing, http, employer):
        job = listing["raw"]
        categories = job.get("categories") or {}
        places = [self._first_place(job)] + list(categories.get("allLocations") or [])
        posted = ""
        if job.get("createdAt"):
            posted = datetime.fromtimestamp(int(job["createdAt"]) / 1000, tz=timezone.utc).date().isoformat()
        return _opening(employer, title=job.get("text", ""), url=job.get("hostedUrl", ""), locations=places,
                        html=(job.get("description") or "") + (job.get("additional") or ""), posted=posted,
                        arrangement=_arrangement(job.get("workplaceType")), schedule=_schedule(categories.get("commitment")))


class Ashby(_WholeBoard):
    def __init__(self, config):
        super().__init__(config)
        self.slug = config["slug"]
        self.base = f"https://api.ashbyhq.com/posting-api/job-board/{self.slug}"

    def _fetch(self, http):
        return [job for job in _get_json(http, self.base).get("jobs") or [] if job.get("isListed", True)]

    def _first_place(self, job):
        return job.get("location") or ""

    def _id_from(self, url):
        match = re.search(r"/([0-9a-f]{8}-[0-9a-f-]{27})", url)
        return match.group(1) if match else None

    @staticmethod
    def _says_in_person(job):
        """True when the posting itself puts the job on site: a LOCATION heading followed by "In-Person" / "On-site" /
        "In office", and nowhere does it say hybrid or remote. Employers often leave Ashby's workplace box on Hybrid."""
        text = str(job.get("descriptionPlain") or "") or re.sub(r"<[^>]+>", "\n", str(job.get("descriptionHtml") or ""))
        if re.search(r"hybrid|remote|work from home|telecommut", text, re.I):
            return False
        return bool(re.search(r"\blocation\b[:\s]*\n?\s*(?:in[- ]?person|on[- ]?site|in[- ]?(?:the[- ])?office)\b", text, re.I))

    def workplace(self, job):
        """Remote, Hybrid or Onsite as Ashby lists the job, or None. Ashby's page data says TELECOMMUTE for hybrid jobs
        too (isRemote is true for them), so workplaceType is the one to trust, unless the posting plainly says the job
        is in person (see _says_in_person)."""
        listed = _arrangement(job.get("workplaceType"))
        return "Onsite" if listed in ("Hybrid", "Remote") and self._says_in_person(job) else listed

    def detail(self, listing, http, employer):
        job = listing["raw"]
        places = [self._first_place(job)] + [place.get("location", "") for place in job.get("secondaryLocations") or []]
        arrangement = self.workplace(job) or ("Remote" if job.get("isRemote") else None)
        return _opening(employer, title=job.get("title", ""), url=job.get("jobUrl", ""), locations=places,
                        html=job.get("descriptionHtml") or job.get("descriptionPlain") or "",
                        posted=str(job.get("publishedAt") or "")[:10], arrangement=arrangement,
                        schedule=_schedule(str(job.get("employmentType") or "").replace("Time", " time")))


class BambooHR(_WholeBoard):
    def __init__(self, config):
        super().__init__(config)
        self.slug = config["slug"]
        self.base = f"https://{self.slug}.bamboohr.com/careers"

    def _fetch(self, http):
        return [dict(job, title=job.get("jobOpeningName")) for job in _get_json(http, self.base + "/list").get("result") or []]

    def _first_place(self, job):
        return self._place(job.get("atsLocation") or job.get("location") or {})

    @staticmethod
    def _place(location):
        return ", ".join(part for part in (location.get("city"), location.get("state") or location.get("stateProvince"),
                                            location.get("country") or location.get("addressCountry")) if part)

    def _id_from(self, url):
        match = re.search(r"/careers/(\d+)|[?&]id=(\d+)", url)
        return (match.group(1) or match.group(2)) if match else None

    def detail(self, listing, http, employer):
        job = listing["raw"]
        info = {}
        try:
            info = _get_json(http, f"{self.base}/{job['id']}/detail").get("result", {}).get("jobOpening", {})
        except (requests.RequestException, ValueError):
            pass
        places = [self._place(info.get("atsLocation") or {}) or self._first_place(job)]
        return _opening(employer, title=info.get("jobOpeningName") or job.get("jobOpeningName", ""),
                        url=info.get("jobOpeningShareUrl") or f"{self.base}/{job['id']}", locations=places,
                        html=info.get("description") or "", posted=str(info.get("datePosted") or "")[:10],
                        arrangement="Remote" if job.get("isRemote") else None,
                        schedule=_schedule(job.get("employmentStatusLabel")))


class Recruitee(_WholeBoard):
    """Recruitee career sites (<company>.recruitee.com): every offer comes from one public JSON address."""

    def __init__(self, config):
        super().__init__(config)
        self.slug = config["slug"]
        self.base = f"https://{self.slug}.recruitee.com/api/offers/"

    def _fetch(self, http):
        return _get_json(http, self.base).get("offers") or []

    def _first_place(self, job):
        return ", ".join(part for part in (job.get("city"), job.get("state_code"), job.get("country_code")) if part) or job.get("location") or ""

    def _id_from(self, url):
        match = re.search(r"/o/([a-z0-9-]+)", url, re.I)
        return match.group(1) if match else None

    def status(self, url, http):
        wanted = self._id_from(url)
        if not wanted:
            return "Unknown"
        return "Open" if any(wanted == str(job.get("slug")) for job in self._fetch(http)) else "Closed"

    def detail(self, listing, http, employer):
        job = listing["raw"]
        arrangement = "Remote" if job.get("remote") else None
        return _opening(employer, title=job.get("title", ""), url=job.get("careers_url") or f"https://{self.slug}.recruitee.com/o/{job.get('slug')}",
                        locations=[self._first_place(job)], html=(job.get("description") or "") + (job.get("requirements") or ""),
                        posted=str(job.get("published_at") or "")[:10], arrangement=arrangement,
                        schedule=_schedule(job.get("employment_type_code")))


class Teamtailor(_WholeBoard):
    """Teamtailor career sites (<company>.teamtailor.com): every opening is in the site's public job feed (jobs.rss)."""

    def __init__(self, config):
        super().__init__(config)
        self.slug = config["slug"]
        self.base = f"https://{self.slug}.teamtailor.com"

    def _fetch(self, http):
        response = http.get(self.base + "/jobs.rss", accept="application/rss+xml,application/xml")
        if response.status_code != 200:
            raise ValueError(f"{self.slug}.teamtailor.com answered HTTP {response.status_code}")
        import xml.etree.ElementTree as ET
        jobs = []
        for item in ET.fromstring(response.content).iter("item"):
            places = [", ".join(part.text.strip() for part in (loc.find("{*}city"), loc.find("{*}country")) if part is not None and part.text)
                      for loc in item.findall(".//{*}location")]
            jobs.append({"id": (item.findtext("guid") or item.findtext("link") or "").strip(), "title": (item.findtext("title") or "").strip(),
                         "link": (item.findtext("link") or "").strip(), "description": item.findtext("description") or "",
                         "posted": (item.findtext("pubDate") or "")[:16], "places": [p for p in places if p],
                         "remote": (item.findtext("{*}remoteStatus") or "").casefold()})
        return jobs

    def _first_place(self, job):
        return (job.get("places") or [""])[0]

    def _id_from(self, url):
        match = re.search(r"/jobs/(\d+)", url)
        return match.group(1) if match else None

    def status(self, url, http):
        wanted = self._id_from(url)
        if not wanted:
            return "Unknown"
        return "Open" if any(f"/jobs/{wanted}" in str(job.get("link")) or wanted in str(job.get("id")) for job in self._fetch(http)) else "Closed"

    def detail(self, listing, http, employer):
        job = listing["raw"]
        remote = job.get("remote", "")
        arrangement = "Remote" if remote == "fully" else "Hybrid" if remote == "hybrid" else None
        return _opening(employer, title=job.get("title", ""), url=job.get("link", ""), locations=job.get("places") or [],
                        html=job.get("description") or "", arrangement=arrangement)


class Rippling(_WholeBoard):
    """Rippling job boards (ats.rippling.com/<company>/jobs): the board's public list has every opening (name, place,
    department); each opening's own record has the text, the work type and the date."""

    def __init__(self, config):
        super().__init__(config)
        self.slug = config["slug"]
        self.base = f"https://api.rippling.com/platform/api/ats/v1/board/{self.slug}/jobs"

    def _fetch(self, http):
        jobs = _get_json(http, self.base)
        return [dict(job, id=str(job.get("uuid") or "").lower(), title=str(job.get("name") or "").strip())
                for job in jobs if isinstance(job, dict) and job.get("uuid")] if isinstance(jobs, list) else []

    def _first_place(self, job):
        return str((job.get("workLocation") or {}).get("label") or "")

    def _id_from(self, url):
        match = re.search(r"/jobs/([0-9a-f]{8}-[0-9a-f-]{27})", url, re.I)
        return match.group(1).lower() if match else None

    def detail(self, listing, http, employer):
        job = listing["raw"]
        info = {}
        try:
            info = _get_json(http, f"{self.base}/{job['id']}")
        except (requests.RequestException, ValueError):
            pass  # the list alone still gives a title, a place and a link
        if not isinstance(info, dict):
            info = {}
        places = [str(place) for place in info.get("workLocations") or [] if place] or [self._first_place(job)]
        words = info.get("description") or {}
        employment = str((info.get("employmentType") or {}).get("label") or "").upper()
        opening = _opening(
            employer, title=job["title"], url=job.get("url") or f"https://ats.rippling.com/{self.slug}/jobs/{job['id']}",
            locations=places, html=(words.get("role") or "") + (words.get("company") or ""),
            posted=str(info.get("createdOn") or "")[:10],
            schedule="FULL_TIME" if employment.endswith("_FT") else "PART_TIME" if employment.endswith("_PT") else "",
            arrangement="Hybrid" if any("hybrid" in place.casefold() for place in places) else None)
        opening["company"] = info.get("companyName") or employer.name
        return opening
