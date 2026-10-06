"""More career-site adapters: SmartRecruiters, ADP, Paylocity, Workable, NeoGov."""

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


class SmartRecruiters:
    """SmartRecruiters company boards (public postings service; searched by keyword)."""

    def __init__(self, config):
        self.config = config
        self.company = config["company"]
        self.base = f"https://api.smartrecruiters.com/v1/companies/{self.company}/postings"

    def owns(self, url):
        return False

    def search(self, keyword, http):
        listings, offset = [], 0
        for _ in range(common.MAX_PAGES):
            data = _get_json(http, self.base, {"q": keyword, "limit": 100, "offset": offset})
            batch = data.get("content") or []
            for job in batch:
                location = job.get("location") or {}
                place = location.get("fullLocation") or ", ".join(
                    part for part in (location.get("city"), location.get("region"), (location.get("country") or "").upper()) if part)
                listings.append({"title": job.get("name", ""), "id": str(job.get("id")), "path": str(job.get("id")),
                                 "location": place, "raw": job})
            offset += 100
            if len(batch) < 100 or offset >= int(data.get("totalFound") or 0):
                break
        return listings

    def detail(self, listing, http, employer):
        job = listing["raw"]
        info = _get_json(http, job["ref"]) if job.get("ref") else {}
        sections = ((info.get("jobAd") or {}).get("sections") or {})
        html = " ".join(str((sections.get(key) or {}).get("text") or "") for key in
                        ("companyDescription", "jobDescription", "qualifications", "additionalInformation"))
        location = job.get("location") or {}
        return _opening(employer, title=job.get("name", ""), url=info.get("postingUrl") or f"https://jobs.smartrecruiters.com/{self.company}/{job['id']}",
                        locations=[listing["location"]], html=html, posted=str(job.get("releasedDate") or "")[:10],
                        arrangement="Remote" if location.get("remote") else None,
                        schedule=_schedule((job.get("typeOfEmployment") or {}).get("label")))

    def status(self, url, http):
        match = re.search(r"/(\d{6,})", urlparse(url).path)
        if not match:
            return "Unknown"
        response = http.get(f"{self.base}/{match.group(1)}")
        return "Open" if response.status_code == 200 else "Closed" if response.status_code in (404, 410) else "Unknown"


class ADP:
    """ADP Workforce Now career centers (recruitment.html?cid=...)."""

    def __init__(self, config):
        self.config = config
        self.host = config.get("host") or "workforcenow.adp.com"
        self.cid, self.cc_id = config["cid"], config.get("ccId") or "19000101_000001"
        self.api = f"https://{self.host}/mascsr/default/careercenter/public/events/staffing/v1/job-requisitions"
        self.params = {"cid": self.cid, "ccId": self.cc_id, "lang": "en_US", "locale": "en_US"}
        self._all = None

    def owns(self, url):
        return (urlparse(url).hostname or "").casefold() == self.host.casefold() and f"cid={self.cid}" in url

    def _fetch(self, http):
        return _get_json(http, self.api, self.params).get("jobRequisitions") or []

    @staticmethod
    def _places(job):
        places = []
        for location in job.get("requisitionLocations") or []:
            address = location.get("address") or {}
            city = address.get("cityName") or ""
            state = (address.get("countrySubdivisionLevel1") or {}).get("codeValue") or ""
            if city:
                places.append(", ".join(part for part in (city, state, "US" if state else "") if part))
        return places

    def search(self, keyword, http):
        if self._all is None:
            self._all = [{"title": job.get("requisitionTitle", ""), "id": str(job.get("itemID")), "path": str(job.get("itemID")),
                          "location": (self._places(job) or [""])[0], "raw": job} for job in self._fetch(http)]
        return self._all

    def detail(self, listing, http, employer):
        job = listing["raw"]
        html = ""
        try:
            html = _get_json(http, f"{self.api}/{job['itemID']}", self.params).get("requisitionDescription") or ""
        except (requests.RequestException, ValueError):
            pass
        remote = any("remote" in str(((place.get("nameCode") or {}).get("shortName") or "")).casefold()
                     for place in job.get("requisitionLocations") or [])
        places = self._places(job) or (["Remote, US"] if remote else [])
        url = (f"https://{self.host}/mascsr/default/mdf/recruitment/recruitment.html?cid={self.cid}&ccId={self.cc_id}"
               f"&jobId={job.get('clientRequisitionID')}&lang=en_US")
        return _opening(employer, title=job.get("requisitionTitle", ""), url=url, locations=places, html=html,
                        posted=str(job.get("postDate") or "")[:10], arrangement="Remote" if remote and len(places) <= 1 else None,
                        schedule=_schedule((job.get("workLevelCode") or {}).get("shortName")))

    def status(self, url, http):
        match = re.search(r"jobId=(\w+)", url)
        if not match:
            return "Unknown"
        return "Open" if any(str(job.get("clientRequisitionID")) == match.group(1) for job in self._fetch(http)) else "Closed"


class Paylocity:
    """Paylocity Recruiting boards: the list page carries every opening as embedded data."""

    def __init__(self, config):
        self.config = config
        self.guid = config["guid"]
        self.list_url = f"https://recruiting.paylocity.com/recruiting/jobs/All/{self.guid}"
        self._all = None

    def owns(self, url):
        return False

    def _fetch(self, http):
        response = http.get(self.list_url, accept=HTML)
        if response.status_code != 200:
            raise ValueError(f"Paylocity answered HTTP {response.status_code}")
        match = re.search(r"window\.pageData\s*=\s*(\{.*?\});\s*</script>", response.text, re.S)
        if not match:
            raise ValueError("Paylocity page had no job data")
        return json.loads(match.group(1)).get("Jobs") or []

    @staticmethod
    def _place(job):
        location = job.get("JobLocation") or {}
        country = "US" if str(location.get("Country") or "").upper() in ("US", "USA") else (location.get("Country") or "")
        return ", ".join(part for part in (location.get("City"), location.get("State"), country) if part)

    def search(self, keyword, http):
        if self._all is None:
            self._all = [{"title": job.get("JobTitle", ""), "id": str(job.get("JobId")), "path": str(job.get("JobId")),
                          "location": self._place(job), "raw": job} for job in self._fetch(http)]
        return self._all

    def detail(self, listing, http, employer):
        job = listing["raw"]
        return _opening(employer, title=job.get("JobTitle", ""), url=f"https://recruiting.paylocity.com/recruiting/jobs/Details/{job['JobId']}",
                        locations=[self._place(job)], html=job.get("Description") or "", posted=str(job.get("PublishedDate") or "")[:10],
                        arrangement="Remote" if job.get("IsRemote") else None)

    def status(self, url, http):
        match = re.search(r"/Details/(\d+)", url, re.I)
        if not match:
            return "Unknown"
        return "Open" if any(str(job.get("JobId")) == match.group(1) for job in self._fetch(http)) else "Closed"


class Workable:
    """Workable career pages (apply.workable.com/<company>)."""

    def __init__(self, config):
        self.config = config
        self.slug = config["slug"]
        self.api = f"https://apply.workable.com/api/v3/accounts/{self.slug}/jobs"
        self.detail_api = f"https://apply.workable.com/api/v2/accounts/{self.slug}/jobs"

    def owns(self, url):
        return False

    def search(self, keyword, http):
        listings, token = [], None
        for _ in range(common.MAX_PAGES):
            payload = {"query": keyword, "location": [], "department": [], "worktype": [], "remote": []}
            if token:
                payload["token"] = token
            response = http.post_json(self.api, payload)
            if response.status_code != 200:
                raise ValueError(f"Workable answered HTTP {response.status_code}")
            data = response.json()
            for job in data.get("results") or []:
                location = job.get("location") or {}
                listings.append({"title": job.get("title", ""), "id": job.get("shortcode"), "path": job.get("shortcode"),
                                 "location": ", ".join(part for part in (location.get("city"), location.get("region"), location.get("countryCode")) if part),
                                 "raw": job})
            token = data.get("nextPage")
            if not token:
                break
        return listings

    def detail(self, listing, http, employer):
        job = _get_json(http, f"{self.detail_api}/{listing['id']}")
        location = job.get("location") or {}
        country = "US" if location.get("countryCode") == "US" else (location.get("country") or "")
        place = ", ".join(part for part in (location.get("city"), location.get("region"), country) if part)
        html = " ".join(str(job.get(key) or "") for key in ("description", "requirements", "benefits"))
        arrangement = _arrangement(job.get("workplace")) or ("Remote" if job.get("remote") else None)
        return _opening(employer, title=job.get("title", ""), url=f"https://apply.workable.com/{self.slug}/j/{listing['id']}/",
                        locations=[place], html=html, posted=str(job.get("published") or "")[:10], arrangement=arrangement,
                        schedule=_schedule(job.get("type")))

    def status(self, url, http):
        match = re.search(r"/j/([A-Za-z0-9]+)", url)
        if not match:
            return "Unknown"
        response = http.get(f"{self.detail_api}/{match.group(1)}")
        return "Open" if response.status_code == 200 else "Closed" if response.status_code in (404, 410) else "Unknown"


class NeoGov:
    """NEOGOV career pages (governmentjobs.com/careers/<agency>): a searchable list, and job pages with JobPosting data."""

    def __init__(self, config):
        self.config = config
        self.agency = config["agency"]
        self.base = "https://www.governmentjobs.com"

    def owns(self, url):
        parsed = urlparse(url)
        return ((parsed.hostname or "").casefold().endswith("governmentjobs.com")
                and f"/careers/{self.agency}/".casefold() in parsed.path.casefold() + "/")

    def search(self, keyword, http):
        listings, seen = [], set()
        for page in range(1, common.MAX_PAGES + 1):
            response = http.get(f"{self.base}/careers/home/index", params={"agency": self.agency, "keyword": keyword, "page": page},
                                accept=HTML, headers={"X-Requested-With": "XMLHttpRequest"})
            if response.status_code != 200:
                raise ValueError(f"NEOGOV answered HTTP {response.status_code}")
            soup = BeautifulSoup(response.text, "html.parser")
            items = soup.select("li.list-item[data-job-id]")
            fresh = 0
            for item in items:
                link = item.select_one("a.item-details-link") or item.select_one("a[href]")
                job_id = item.get("data-job-id")
                if not link or not job_id or job_id in seen:
                    continue
                seen.add(job_id)
                fresh += 1
                meta = [li.get_text(" ", strip=True) for li in item.select("ul.list-meta > li")]
                listings.append({"title": link.get_text(" ", strip=True), "id": job_id, "path": link.get("href", ""),
                                 "location": meta[0] if meta else ""})
            if not fresh:
                break
        return listings

    def detail(self, listing, http, employer):
        response = http.get(self.base + listing["path"], accept=HTML)
        if response.status_code != 200:
            return None
        soup = BeautifulSoup(response.text, "html.parser")
        posting = None
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(script.string or "")
            except ValueError:
                continue
            for node in data if isinstance(data, list) else [data]:
                if isinstance(node, dict) and node.get("@type") == "JobPosting":
                    posting = node
                    break
        if not posting:
            return None
        places = []
        for location in posting.get("jobLocation") if isinstance(posting.get("jobLocation"), list) else [posting.get("jobLocation")]:
            address = (location or {}).get("address") or {}
            place = str(address.get("addressLocality") or "").strip()
            region = str(address.get("addressRegion") or "").strip()
            if place and region and not re.search(r"\b" + re.escape(region) + r"\b", place):
                place += f", {region}"
            if place and str(address.get("addressCountry") or "").upper() in ("US", "USA", "UNITED STATES"):
                place += ", US"
            if place:
                places.append(place)
        kind = str(posting.get("employmentType") or "")
        return _opening(employer, title=posting.get("title") or listing["title"],
                        url=self.base + listing["path"], locations=places or [listing["location"] + ", US"],
                        html=posting.get("description") or "", posted=str(posting.get("datePosted") or "")[:10],
                        schedule=_schedule(kind))

    def status(self, url, http):
        response = http.get(url, accept=HTML)
        if response.status_code in (404, 410):
            return "Closed"
        if response.status_code != 200:
            return "Unknown"
        if re.search(r"no longer (?:available|accepting)|posting (?:has )?(?:closed|expired)|job (?:is )?closed", response.text, re.I):
            return "Closed"
        return "Open" if '"JobPosting"' in response.text else "Closed"
