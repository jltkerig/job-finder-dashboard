"""Career-site adapters for big employer systems: Workday, Oracle, iCIMS, SuccessFactors, UltiPro."""

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


class Workday:
    def __init__(self, config):
        self.host, self.tenant, self.site = config["host"], config["tenant"], config["site"]
        self.base = f"https://{self.host}/wday/cxs/{self.tenant}/{self.site}"

    def owns(self, url):
        return (urlparse(url).hostname or "").casefold() == self.host.casefold()

    def search(self, keyword, http):
        listings, offset = [], 0
        for _ in range(common.MAX_PAGES):
            response = http.post_json(self.base + "/jobs", {"appliedFacets": {}, "limit": 20, "offset": offset,
                                                              "searchText": keyword})
            if response.status_code != 200:
                raise ValueError(f"Workday answered HTTP {response.status_code}")
            data = response.json()
            posts = data.get("jobPostings", [])
            listings += [{"title": post.get("title", ""), "path": post.get("externalPath", ""),
                          "location": post.get("locationsText", ""), "id": post.get("externalPath", "")}
                         for post in posts if post.get("externalPath")]
            offset += 20
            if len(posts) < 20 or offset >= int(data.get("total") or 0):
                break
        return listings

    def detail(self, listing, http, employer):
        response = http.get(self.base + listing["path"])
        if response.status_code != 200:
            return None
        info = response.json().get("jobPostingInfo") or {}
        if not info.get("title"):
            return None
        country = str((info.get("country") or {}).get("descriptor") or "")
        suffix = ", US" if "united states" in country.casefold() else (f", {country}" if country else "")
        raw = [str(info.get("location") or "")] + [str(place) for place in (info.get("additionalLocations") or [])]
        locations = [place + suffix for place in raw if place]
        virtual = any("virtual" in place.casefold() for place in raw)
        arrangement = _arrangement(info.get("remoteType")) or ("Remote" if virtual else None)
        # e.g. "GEORGIA - VIRTUAL - GA01": a remote job for residents of one state.
        states = {match.group(1) for place in raw for match in [re.search(r"\b([A-Z]{2})\d{2}\b", place)] if match} if virtual else set()
        url = info.get("externalUrl") or f"https://{self.host}/{self.site}{listing['path']}"
        return _opening(employer, title=info["title"], url=url, locations=locations, html=info.get("jobDescription"),
                        posted=info.get("startDate"), schedule=_schedule(info.get("timeType")), arrangement=arrangement,
                        country=country, remote_states=states)

    def remote_limits(self, url, http):
        """States named by the posting's "Work At Home-<State>" style locations (empty when it names none)."""
        match = re.search(r"/job/.+$", urlparse(url).path)
        response = http.get(self.base + match.group(0)) if match else None
        if response is None or response.status_code != 200:
            return set()
        info = response.json().get("jobPostingInfo") or {}
        return place_states([str(info.get("location") or "")] + [str(place) for place in info.get("additionalLocations") or []])

    def status(self, url, http):
        match = re.search(r"/job/.+$", urlparse(url).path)
        if not match:
            return "Unknown"
        response = http.get(self.base + match.group(0))
        if response.status_code in (404, 410):
            return "Closed"
        if response.status_code != 200:
            return "Unknown"
        info = response.json().get("jobPostingInfo") or {}
        if not info or info.get("posted") is False:
            return "Closed"
        end = str(info.get("endDate") or "")[:10]
        return "Closed" if end and end < date.today().isoformat() else "Open"


class Oracle:
    def __init__(self, config):
        self.host, self.site = config["host"], config["site"]
        self.base = f"https://{self.host}/hcmRestApi/resources/latest"

    def owns(self, url):
        return (urlparse(url).hostname or "").casefold() == self.host.casefold()

    def search(self, keyword, http):
        keyword = re.sub(r"[,;=\"]", " ", keyword)
        response = http.get(self.base + "/recruitingCEJobRequisitions", params={
            "onlyData": "true", "expand": "requisitionList.secondaryLocations",
            "finder": f"findReqs;siteNumber={self.site},limit=25,keyword={keyword},sortBy=POSTING_DATES_DESC"})
        if response.status_code != 200:
            raise ValueError(f"Oracle answered HTTP {response.status_code}")
        items = response.json().get("items") or [{}]
        return [{"title": req.get("Title", ""), "id": str(req.get("Id")), "path": str(req.get("Id")),
                 "location": req.get("PrimaryLocation", ""), "raw": req} for req in items[0].get("requisitionList", [])
                if req.get("Id")]

    def detail(self, listing, http, employer):
        raw = listing["raw"]
        html = raw.get("ShortDescriptionStr") or ""
        try:
            response = http.get(self.base + "/recruitingCEJobRequisitionDetails", params={
                "expand": "all", "onlyData": "true", "finder": f'ById;Id="{listing["id"]}",siteNumber={self.site}'})
            if response.status_code == 200 and response.json().get("items"):
                body = response.json()["items"][0]
                html = " ".join(str(body.get(key) or "") for key in
                                ("ExternalDescriptionStr", "ExternalResponsibilitiesStr", "ExternalQualificationsStr")).strip() or html
        except (requests.RequestException, ValueError):
            pass
        locations = [raw.get("PrimaryLocation", "")] + [place.get("Name", "") for place in raw.get("secondaryLocations") or []]
        arrangement = _arrangement(raw.get("WorkplaceType") or raw.get("WorkplaceTypeCode"))
        url = f"https://{self.host}/hcmUI/CandidateExperience/en/sites/{self.site}/job/{listing['id']}"
        return _opening(employer, title=raw.get("Title", ""), url=url, locations=locations, html=html,
                        posted=raw.get("PostedDate"), arrangement=arrangement, country="",
                        category=raw.get("JobFamily") or "")

    def status(self, url, http):
        match = re.search(r"/job/(\d+)", urlparse(url).path)
        if not match:
            return "Unknown"
        response = http.get(self.base + "/recruitingCEJobRequisitionDetails", params={
            "expand": "all", "onlyData": "true", "finder": f'ById;Id="{match.group(1)}",siteNumber={self.site}'})
        if response.status_code in (404, 410):
            return "Closed"
        if response.status_code != 200:
            return "Unknown"
        return "Open" if response.json().get("items") else "Closed"


class ICIMS:
    """iCIMS career sites: the search page lists jobs and each job page carries a JobPosting record."""

    def __init__(self, config):
        self.host = config["host"]
        self.base = f"https://{self.host}"

    def owns(self, url):
        return (urlparse(url).hostname or "").casefold() == self.host.casefold()

    def search(self, keyword, http):
        response = http.get(self.base + "/jobs/search", params={"ss": 1, "searchKeyword": keyword, "in_iframe": 1}, accept=HTML)
        if response.status_code != 200:
            raise ValueError(f"iCIMS answered HTTP {response.status_code}")
        listings, seen = [], set()
        for link in BeautifulSoup(response.text, "html.parser").find_all("a", href=re.compile(r"/jobs/\d+/")):
            match = re.search(r"/jobs/(\d+)/", link["href"])
            heading = link.find(["h3", "h2"])
            title = re.sub(r"^Title\s+", "", (heading or link).get_text(" ", strip=True))
            if match and match.group(1) not in seen and title:
                seen.add(match.group(1))
                listings.append({"title": title, "id": match.group(1), "path": link["href"], "location": ""})
        return listings

    def _public(self, path):
        return urlparse(urljoin(self.base + "/", path))._replace(query="", fragment="").geturl()

    def detail(self, listing, http, employer):
        response = http.get(urljoin(self.base + "/", listing["path"]), accept=HTML)
        if response.status_code != 200:
            return None
        found = extract_jobs(self._public(listing["path"]), response.text, [listing["title"]])
        if not found:
            return None
        job = found[0]
        return _opening(employer, title=job["title"], url=self._public(listing["path"]), locations=[job["location"]],
                        html=job["description"], posted=job["posted"], schedule=job["schedule"], arrangement=job["type"])

    def status(self, url, http):
        response = http.get(url, accept=HTML)
        if response.status_code in (404, 410):
            return "Closed"
        if response.status_code != 200:
            return "Unknown"
        return "Open" if extract_jobs(url, response.text, [""]) or '"JobPosting"' in response.text else "Closed"


class SuccessFactors:
    """SuccessFactors career sites: a public search page, and job pages marked up with schema.org microdata."""

    def __init__(self, config):
        self.host = config["host"]
        self.base = f"https://{self.host}"

    def owns(self, url):
        return (urlparse(url).hostname or "").casefold() == self.host.casefold()

    def search(self, keyword, http):
        listings, seen = [], set()
        for start in (0, 25):
            response = http.get(self.base + "/search/", params={"q": keyword, "locationsearch": "", "startrow": start}, accept=HTML)
            if response.status_code != 200:
                raise ValueError(f"SuccessFactors answered HTTP {response.status_code}")
            found = BeautifulSoup(response.text, "html.parser").select("a.jobTitle-link")
            for link in found:
                href = link.get("href", "")
                if href and href not in seen:
                    seen.add(href)
                    listings.append({"title": link.get_text(" ", strip=True), "id": href, "path": href, "location": ""})
            if len(found) < 25:
                break
        return listings

    @staticmethod
    def _prop(soup, name):
        tag = soup.find(attrs={"itemprop": name})
        return (tag.get("content") or tag.get_text(" ", strip=True)).strip() if tag else ""

    def detail(self, listing, http, employer):
        response = http.get(self.base + listing["path"], accept=HTML)
        if response.status_code != 200:
            return None
        soup = BeautifulSoup(response.text, "html.parser")
        title = self._prop(soup, "title") or listing["title"]
        place = ", ".join(part for part in (self._prop(soup, "addressLocality"), self._prop(soup, "addressRegion"),
                                             self._prop(soup, "addressCountry")) if part)
        description = soup.find(attrs={"itemprop": "description"}) or soup.select_one("span.jobdescription")
        posted = ""
        try:
            posted = datetime.strptime(self._prop(soup, "datePosted"), "%a %b %d %H:%M:%S UTC %Y").date().isoformat()
        except ValueError:
            pass
        return _opening(employer, title=title, url=self.base + listing["path"], locations=[place],
                        html=str(description or ""), posted=posted)

    def status(self, url, http):
        response = http.get(url, accept=HTML)
        if response.status_code in (404, 410):
            return "Closed"
        if response.status_code != 200:
            return "Unknown"
        return "Open" if BeautifulSoup(response.text, "html.parser").find(attrs={"itemprop": "title"}) else "Closed"


class UltiPro:
    """UKG / UltiPro Recruiting job boards (recruiting.ultipro.com/<tenant>/JobBoard/<board>)."""
    MAX_JOBS = 200

    def __init__(self, config):
        self.host = config.get("host") or "recruiting.ultipro.com"
        self.tenant, self.board = config["tenant"], config["board"]
        self.base = f"https://{self.host}/{self.tenant}/JobBoard/{self.board}"
        self._all = None

    def owns(self, url):
        parsed = urlparse(url)
        return ((parsed.hostname or "").casefold() == self.host.casefold()
                and f"/{self.tenant}/".casefold() in (parsed.path + "/").casefold())

    def search(self, keyword, http):
        # These boards are small: read every opening once and let title matching pick.
        if self._all is None:
            jobs, skip = [], 0
            while skip < self.MAX_JOBS:
                response = http.post_json(self.base + "/JobBoardView/LoadSearchResults", {
                    "opportunitySearch": {"Top": 50, "Skip": skip, "QueryString": "", "Filters": [],
                                          "OrderBy": [{"Value": "postedDateDesc", "PropertyName": "PostedDate", "Ascending": False}]},
                    "matchCriteria": {"PreferredJobs": [], "Educations": [], "LicenseAndCertifications": [], "Skills": [],
                                      "hasNoLicenses": False, "SkippedSkills": []}})
                if response.status_code != 200:
                    raise ValueError(f"UltiPro answered HTTP {response.status_code}")
                data = response.json()
                batch = data.get("opportunities") or []
                jobs += batch
                skip += 50
                if len(batch) < 50 or skip >= int(data.get("totalCount") or 0):
                    break
            self._all = [{"title": job.get("Title", ""), "id": str(job.get("Id")), "path": str(job.get("Id")),
                          "location": self._place((job.get("Locations") or [{}])[0]), "raw": job}
                         for job in jobs if job.get("Id")]
        return self._all

    @staticmethod
    def _place(location):
        address = (location or {}).get("Address") or {}
        country = address.get("Country") or {}
        suffix = "US" if str(country.get("Code") or "").upper() in ("US", "USA") else (country.get("Name") or country.get("Code") or "")
        return ", ".join(part for part in (address.get("City"), (address.get("State") or {}).get("Code"), suffix) if part)

    def detail(self, listing, http, employer):
        raw = listing["raw"]
        url = f"{self.base}/OpportunityDetail?opportunityId={raw['Id']}"
        html = raw.get("BriefDescription") or ""
        try:
            page = http.get(url, accept=HTML)
            if page.status_code == 200:
                match = re.search(r'"Description":"((?:[^"\\]|\\.)*)"', page.text)
                if match:
                    html = json.loads('"' + match.group(1) + '"')
        except (requests.RequestException, ValueError):
            pass
        locations = [self._place(place) for place in raw.get("Locations") or []]
        country = ((((raw.get("Locations") or [{}])[0].get("Address") or {}).get("Country")) or {}).get("Name") or ""
        full_time = raw.get("FullTime")
        return _opening(employer, title=raw.get("Title", ""), url=url, locations=locations, html=html,
                        posted=str(raw.get("PostedDate") or "")[:10], country=country,
                        schedule="FULL_TIME" if full_time else "PART_TIME" if full_time is False else "",
                        category=raw.get("JobCategoryName") or "")

    def status(self, url, http):
        response = http.get(url, accept=HTML)
        if response.status_code in (404, 410):
            return "Closed"
        if response.status_code != 200:
            return "Unknown"
        return "Open" if '"RequisitionNumber"' in response.text else "Closed"


