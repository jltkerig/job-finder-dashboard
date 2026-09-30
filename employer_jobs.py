"""Search big employers' own career sites (Workday, Oracle, iCIMS, SuccessFactors) for openings
that match the user's titles.

Workday and Oracle publish the same search their career pages use. iCIMS and SuccessFactors
sites are read from their public search and job pages. No login is needed for any of them. Which employers to search lives in watched_employers.json; DEFAULT_EMPLOYERS is used when that
file is missing.
"""
import html as html_lib
import json
import re
import time
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from job_listings import extract_jobs, matching_title

SOURCE_TYPE = "Employer careers"
CONFIG_FILE = Path(__file__).resolve().parent / "watched_employers.json"
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
    text = str(text or "").casefold()
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


ADAPTERS = {"workday": Workday, "oracle": Oracle, "icims": ICIMS, "successfactors": SuccessFactors}


class Employer:
    def __init__(self, config):
        self.config = config
        self.name, self.system = config["name"], config["system"]
        self.domain = config.get("domain") or urlparse("//" + config["host"]).hostname
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


def load_employers(path=CONFIG_FILE):
    """Employers to search, from watched_employers.json (a list) or the built-in defaults."""
    try:
        configs = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(configs, list):
            raise ValueError("watched_employers.json must hold a list")
    except (OSError, ValueError):
        configs = DEFAULT_EMPLOYERS
    employers = []
    for config in configs:
        if not isinstance(config, dict) or config.get("enabled", True) is False:
            continue
        if config.get("system") in ADAPTERS and all(key in config for key in ("name", "host")):
            try:
                employers.append(Employer(config))
            except KeyError:
                continue
    return employers


def employer_for_url(url, employers):
    return next((employer for employer in employers if employer.adapter.owns(url)), None)
