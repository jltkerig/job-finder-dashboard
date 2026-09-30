"""Search big employers' own career sites (Workday, Oracle, iCIMS, SuccessFactors, UltiPro,
Greenhouse, Lever, Ashby, BambooHR, SmartRecruiters, ADP, Paylocity, Workable) for openings
that match the user's titles.

Workday and Oracle publish the same search their career pages use. iCIMS and SuccessFactors
sites are read from their public search and job pages. No login is needed for any of them. Which employers to search lives in watched_employers.json; DEFAULT_EMPLOYERS is used when that
file is missing.
"""
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

from job_listings import extract_jobs, matching_title

SOURCE_TYPE = "Employer careers"
CONFIG_FILE = Path(__file__).resolve().parent / "watched_employers.json"
# Boards found in web searches; managed by Job Finder and searched again on later runs.
DISCOVERED_FILE = Path(__file__).resolve().parent / "discovered_employers.json"
MAX_DISCOVERED = 40
USER_AGENT = "Mozilla/5.0 (compatible; PersonalJobFinder/1.1; local job search)"
MAX_KEYWORDS = 16
MAX_PAGES = 3
MAX_DETAILS = 30

DEFAULT_EMPLOYERS = [
    {"name": "The Home Depot", "system": "workday", "host": "homedepot.wd5.myworkdayjobs.com", "tenant": "homedepot",
     "site": "CareerDepot", "domain": "homedepot.com",
     # Corporate technology and design roles only: no distribution-center or store-level jobs.
     "title_keywords": ["engineer", "developer", "software", "data", "analyst", "architect", "ux", "ui", "design",
                        "product", "technolog", "devops", "cloud", "security", "machine learning", "web", "digital",
                        "content", "creative", "graphic", "visual", "producer", "program"],
     "exclude_locations": ["DFC", "DISTRIBUTION", "FULFILLMENT", "SUPPLY CHAIN"],
     "extra_titles": ["UX Designer", "UI Designer", "Digital Designer", "Front End Developer", "Web Developer"]},
    {"name": "Capital One", "system": "workday", "host": "capitalone.wd12.myworkdayjobs.com", "tenant": "capitalone",
     "site": "Capital_One", "domain": "capitalone.com"},
    {"name": "T. Rowe Price", "system": "workday", "host": "troweprice.wd5.myworkdayjobs.com", "tenant": "troweprice",
     "site": "TRowePrice", "domain": "troweprice.com"},
    {"name": "Barclays", "system": "workday", "host": "barclays.wd3.myworkdayjobs.com", "tenant": "barclays",
     "site": "External_Career_Site_Barclays", "domain": "barclays.com"},
    {"name": "WSFS Bank", "system": "workday", "host": "wsfsbank.wd1.myworkdayjobs.com", "tenant": "wsfsbank",
     "site": "wsfscareers", "domain": "wsfsbank.com"},
    {"name": "DuPont", "system": "workday", "host": "dupont.wd5.myworkdayjobs.com", "tenant": "dupont",
     "site": "Jobs", "domain": "dupont.com"},
    {"name": "ChristianaCare", "system": "workday", "host": "christianacare.wd5.myworkdayjobs.com",
     "tenant": "christianacare", "site": "CCHS", "domain": "christianacare.org"},
    {"name": "JPMorgan Chase", "system": "oracle", "host": "jpmc.fa.oraclecloud.com", "site": "CX_1001",
     "domain": "jpmorganchase.com"},
    {"name": "Sinclair", "system": "oracle", "host": "edyy.fa.us2.oraclecloud.com", "site": "CX_2002",
     "domain": "sbgi.net"},
    {"name": "Chemours", "system": "workday", "host": "chemours.wd103.myworkdayjobs.com", "tenant": "chemours",
     "site": "Chemours", "domain": "chemours.com"},
    {"name": "Under Armour", "system": "successfactors", "host": "careers.underarmour.com", "domain": "underarmour.com"},
    {"name": "McCormick", "system": "successfactors", "host": "careers.mccormick.com", "domain": "mccormick.com"},
    {"name": "Allegis Group", "system": "icims", "host": "careers-allegisgroup.icims.com", "domain": "allegisgroup.com"},
    {"name": "The Agora Companies", "system": "ultipro", "host": "recruiting.ultipro.com", "tenant": "WAD1002WADM",
     "board": "be1bb296-2bff-4732-8fa5-4c8775112887", "domain": "theagora.com"},
]


class Http:
    """requests with a user agent, a timeout, and a short pause between calls."""

    def __init__(self, delay=0.4, timeout=30):
        self.delay, self.timeout, self._last = delay, timeout, 0.0

    def _wait(self):
        pause = self.delay - (time.monotonic() - self._last)
        if pause > 0:
            time.sleep(pause)
        self._last = time.monotonic()

    def get(self, url, params=None, accept="application/json"):
        self._wait()
        return requests.get(url, params=params, headers={"User-Agent": USER_AGENT, "Accept": accept},
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
    return {"title": title[:255], "url": url, "company": employer.name, "location": locations[0] if locations else "",
            "locations": locations, "type": arrangement, "schedule": schedule, "salary": "", "posted": posted or None,
            "evidence": [f"{employer.name} careers site ({employer.system.title()})", "title matches search"],
            "description": _plain(html), "html": str(html or ""), "country": country,
            "remote_states": set(remote_states), "category": category}


class Workday:
    def __init__(self, config):
        self.host, self.tenant, self.site = config["host"], config["tenant"], config["site"]
        self.base = f"https://{self.host}/wday/cxs/{self.tenant}/{self.site}"

    def owns(self, url):
        return (urlparse(url).hostname or "").casefold() == self.host.casefold()

    def search(self, keyword, http):
        listings, offset = [], 0
        for _ in range(MAX_PAGES):
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


HTML = "text/html,application/xhtml+xml"


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

    def detail(self, listing, http, employer):
        job = listing["raw"]
        places = [self._first_place(job)] + [place.get("location", "") for place in job.get("secondaryLocations") or []]
        arrangement = _arrangement(job.get("workplaceType")) or ("Remote" if job.get("isRemote") else None)
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
        for _ in range(MAX_PAGES):
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
        for _ in range(MAX_PAGES):
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


ADAPTERS = {"workday": Workday, "oracle": Oracle, "icims": ICIMS, "successfactors": SuccessFactors, "ultipro": UltiPro,
            "greenhouse": Greenhouse, "lever": Lever, "ashby": Ashby, "bamboohr": BambooHR, "smartrecruiters": SmartRecruiters,
            "adp": ADP, "paylocity": Paylocity, "workable": Workable}


class Employer:
    def __init__(self, config):
        self.config = config
        self.name, self.system = config["name"], config["system"]
        self.discovered = bool(config.get("discovered"))
        self.domain = config.get("domain") or urlparse("//" + (config.get("host") or "boards.example")).hostname
        self.extra_titles = list(config.get("extra_titles") or [])
        self.title_keywords = [word.casefold() for word in config.get("title_keywords") or []]
        self.exclude_locations = [place.casefold() for place in config.get("exclude_locations") or []]
        self.adapter = ADAPTERS[self.system](config)

    def allows_location(self, place):
        place = str(place or "").casefold()
        return not any(excluded in place for excluded in self.exclude_locations)

    def allows_title(self, title):
        title = str(title or "").casefold()
        return not self.title_keywords or any(word in title for word in self.title_keywords)

    def find_openings(self, titles, http):
        """Openings whose title matches one of the titles (or this employer's extra titles), with details."""
        wanted = list(titles) + self.extra_titles
        keywords = list(dict.fromkeys(title.casefold().strip() for title in wanted if title.strip()))[:MAX_KEYWORDS]
        listings = {}
        for keyword in keywords:
            for listing in self.adapter.search(keyword, http):
                listings.setdefault(listing["id"], listing)
        chosen = [listing for listing in listings.values()
                  if matching_title(listing["title"], wanted) and self.allows_title(listing["title"])
                  and self.allows_location(listing["location"])]
        openings = []
        for listing in chosen[:MAX_DETAILS]:
            opening = self.adapter.detail(listing, http, self)
            if not opening:
                continue
            opening["locations"] = [place for place in opening["locations"] if self.allows_location(place)]
            if opening["locations"]:
                opening["location"] = opening["locations"][0]
                openings.append(opening)
        return openings

    def status(self, url, http):
        """'Open', 'Closed' or 'Unknown' for one of this employer's posting URLs."""
        return self.adapter.status(url, http)


_ID_FIELDS = ("system", "host", "tenant", "site", "board", "slug", "company", "eu", "cid", "ccId", "guid")


def config_key(config):
    """A stable identity for a board, so the same one is never searched twice."""
    return json.dumps({field: str(config[field]).casefold() for field in _ID_FIELDS if config.get(field) not in (None, "")},
                      sort_keys=True)


def _read_list(path):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, list) else None


def load_employers(path=CONFIG_FILE, discovered_path=DISCOVERED_FILE):
    """Employers to search: watched_employers.json (or the defaults), then boards found in earlier searches."""
    configs = _read_list(path)
    if configs is None:
        configs = DEFAULT_EMPLOYERS
    configs = list(configs) + [dict(config, discovered=True) for config in (_read_list(discovered_path) or [])
                               if isinstance(config, dict)]
    employers, seen = [], set()
    for config in configs:
        if not isinstance(config, dict) or config.get("enabled", True) is False:
            continue
        if config.get("system") not in ADAPTERS or "name" not in config:
            continue
        try:
            employer = Employer(config)
        except KeyError:
            continue
        key = config_key(config)
        if key not in seen:
            seen.add(key)
            employers.append(employer)
    return employers


def save_discovered(config, path=DISCOVERED_FILE, cap=MAX_DISCOVERED):
    """Remember a board found in a web search; keeps the newest `cap` boards."""
    stored = [item for item in (_read_list(path) or []) if isinstance(item, dict)]
    today = date.today().isoformat()
    key = config_key(config)
    for item in stored:
        if config_key(item) == key:
            item["last_seen"] = today
            break
    else:
        stored.append(dict({k: v for k, v in config.items() if k != "discovered"}, first_seen=today, last_seen=today))
    stored.sort(key=lambda item: item.get("last_seen", ""), reverse=True)
    temporary = Path(str(path) + ".tmp")
    try:
        temporary.write_text(json.dumps(stored[:cap], indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, path)
    except OSError:
        pass


def employer_for_url(url, employers):
    return next((employer for employer in employers if employer.adapter.owns(url)), None)
