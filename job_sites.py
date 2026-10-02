"""Job sites that list many employers' jobs and are searched by title and place, rather than one company's board.

National Labor Exchange (usnlx.com, run by DirectEmployers and NASWA): many employers near Aberdeen Proving Ground
(ManTech, SURVICE, ...) post here. The site's own app reads jobs from DirectEmployers' search service; Job Finder asks
the same service, politely: it says who it is, waits between requests, reads only a couple of pages per search,
and stops for the rest of the run if the site pushes back (HTTP 403/429). robots.txt allows /jobs/ (only feeds are
disallowed, and they are not used).

Not searched: the Maryland Workforce Exchange (mwejobs.maryland.gov). Its robots.txt disallows every automated
visitor except search engines ("User-agent: * / Disallow: /"), so Job Finder leaves it alone. The National Labor
Exchange is one of the main sources state job banks like it draw on, so many of the same jobs arrive through NLX.
"""

import html as html_lib
import os
import re
import time
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import requests

from places import STATE_NAMES

USER_AGENT = "Mozilla/5.0 (compatible; PersonalJobFinder/1.1; local job search)"


class SiteBlocked(Exception):
    """The site asked us to slow down or refused; leave it alone for the rest of the run."""


class NationalLaborExchange:
    name = "National Labor Exchange"
    domain = "usnlx.com"
    API = "https://prod-search-api.jobsyn.org/api/v1/solr/jobs"
    PAGE_SIZE = 15  # fixed by the service
    MAX_PAGES = 2  # per title and place: the 30 most relevant openings
    DELAY = 1.5  # seconds between requests

    def __init__(self, timeout=30):
        self.timeout = timeout
        self._last = 0.0
        self.requests = 0

    def _get(self, params):
        pause = self.DELAY - (time.monotonic() - self._last)
        if pause > 0:
            time.sleep(pause)
        self._last = time.monotonic()
        self.requests += 1
        response = requests.get(self.API, params=params, timeout=self.timeout, headers={
            "User-Agent": USER_AGENT, "Accept": "application/json", "x-origin": "usnlx.com"})
        if response.status_code in (403, 429):
            raise SiteBlocked(f"{self.name} answered HTTP {response.status_code}")
        if response.status_code != 200:
            raise ValueError(f"{self.name} answered HTTP {response.status_code}")
        return response.json()

    @staticmethod
    def job_url(guid):
        return f"https://usnlx.com/{guid}/job/"

    @staticmethod
    def guid_from(url):
        match = re.search(r"/([0-9A-Fa-f]{32})/job", str(url or ""))
        return match.group(1).upper() if match else None

    def _listing(self, job):
        guid = str(job.get("guid") or "").upper()
        city, state = job.get("city_exact") or "", job.get("state_short_exact") or ""
        location = job.get("location_exact") or ", ".join(part for part in (city, state) if part)
        lat = lon = None
        try:
            lat, lon = (float(part) for part in str(job.get("GeoLocation") or "").split(","))
        except ValueError:
            pass
        posted = str(job.get("date_new") or job.get("date_added") or "")[:10]
        description = html_lib.unescape(str(job.get("description") or "")).strip()
        return {
            "guid": guid, "title": str(job.get("title_exact") or "").strip(), "company": str(job.get("company_exact") or "").strip(),
            "url": self.job_url(guid), "location": location, "city": city, "state": state, "country": job.get("country_exact") or "",
            "lat": lat, "lon": lon, "miles": job.get("miles"), "posted": posted, "description": description,
        }

    def search(self, keyword, place="", radius=None):
        """Openings for a title near a place (a city, ZIP code or state), most relevant first."""
        listings = []
        for page in range(1, self.MAX_PAGES + 1):
            params = {"q": keyword, "page": page}
            if place:
                params["location"] = place
            if radius:
                params["r"] = int(radius)
            data = self._get(params)
            listings.extend(self._listing(job) for job in data.get("jobs") or [] if job.get("guid"))
            if not (data.get("pagination") or {}).get("has_more_pages"):
                break
        return listings

    def status(self, url):
        """'Open' while the job is still listed, 'Closed' once it is gone, 'Unknown' if the site can't be asked."""
        guid = self.guid_from(url)
        if not guid:
            return "Unknown"
        try:
            data = self._get({"q": f"guid:{guid}"})
        except (requests.RequestException, ValueError, SiteBlocked):
            return "Unknown"
        return "Open" if int((data.get("pagination") or {}).get("total") or 0) > 0 else "Closed"


class USAJobs:
    """USAJOBS.gov, the federal government's job board, through its official search API (data.usajobs.gov).

    The API is free but needs a personal key: request one at developer.usajobs.gov (it is tied to your email),
    then put USAJOBS_API_KEY and USAJOBS_EMAIL in the .env file. Without them this site is skipped. The email is
    sent as the User-Agent, as the API requires. Only openings open to the public (WhoMayApply=public) from the
    last 30 days are read. The API cannot look up one announcement, so a saved job's status stays "Unknown".
    """
    name = "USAJOBS"
    domain = "usajobs.gov"
    API = "https://data.usajobs.gov/api/search"
    PAGE_SIZE = 25
    MAX_PAGES = 2  # per title and place
    DELAY = 1.0  # seconds between requests
    DAYS = 30

    def __init__(self, timeout=30):
        self.timeout = timeout
        self._last = 0.0
        self.requests = 0
        self.key = os.getenv("USAJOBS_API_KEY", "").strip()
        self.email = os.getenv("USAJOBS_EMAIL", "").strip()

    @property
    def configured(self):
        return bool(self.key and self.email)

    setup_hint = "Add USAJOBS_API_KEY and USAJOBS_EMAIL to .env (free key: developer.usajobs.gov)."

    def _get(self, params):
        pause = self.DELAY - (time.monotonic() - self._last)
        if pause > 0:
            time.sleep(pause)
        self._last = time.monotonic()
        self.requests += 1
        response = requests.get(self.API, params=params, timeout=self.timeout, headers={
            "Host": "data.usajobs.gov", "User-Agent": self.email, "Authorization-Key": self.key})
        if response.status_code == 429:
            raise SiteBlocked(f"{self.name} answered HTTP 429")
        if response.status_code in (401, 403):
            raise ValueError(f"{self.name} refused the API key (HTTP {response.status_code}). "
                             "Check USAJOBS_API_KEY and USAJOBS_EMAIL in .env.")
        if response.status_code != 200:
            raise ValueError(f"{self.name} answered HTTP {response.status_code}")
        return response.json()

    @staticmethod
    def job_url(job_id):
        return f"https://www.usajobs.gov/job/{job_id}"

    @staticmethod
    def location_name(place):
        """"Baltimore, MD" -> "Baltimore, Maryland": the API's location names spell the state out."""
        city, _, state = str(place).partition(",")
        full = STATE_NAMES.get(state.strip().upper())
        return f"{city.strip()}, {full}" if full else str(place).strip()

    def _listing(self, item):
        job = item.get("MatchedObjectDescriptor") or {}
        job_id = str(job.get("PositionID") or item.get("MatchedObjectId") or "").strip()
        places = job.get("PositionLocation") or []
        first = places[0] if places else {}
        shown = str(first.get("LocationName") or job.get("PositionLocationDisplay") or "").strip()
        remote = bool(re.search(r"anywhere in the u\.?s|remote", shown, re.I))
        location = "United States (Remote)" if remote else shown
        city = "" if remote else shown.split(",")[0].strip()
        state = "" if remote else str(first.get("CountrySubDivisionCode") or "").strip()
        lat = lon = None
        try:
            lat, lon = float(first.get("Latitude")), float(first.get("Longitude"))
        except (TypeError, ValueError):
            pass
        details = (job.get("UserArea") or {}).get("Details") or {}
        duties = details.get("MajorDuties")
        parts = [details.get("JobSummary"), "\n".join(duties) if isinstance(duties, list) else duties,
                 job.get("QualificationSummary"), details.get("Requirements"), details.get("Education")]
        pay = (job.get("PositionRemuneration") or [{}])[0]
        if pay.get("MinimumRange"):
            parts.append(f"Pay: ${pay.get('MinimumRange')} - ${pay.get('MaximumRange')} per {pay.get('Description') or 'year'}")
        description = "\n\n".join(html_lib.unescape(str(part)).strip() for part in parts if part)
        return {
            "guid": job_id, "title": str(job.get("PositionTitle") or "").strip(),
            "company": str(job.get("OrganizationName") or job.get("DepartmentName") or "").strip(),
            "url": self.job_url(job_id), "location": location, "city": city, "state": state,
            "country": str(first.get("CountryCode") or "United States").strip(), "lat": lat, "lon": lon, "miles": None,
            "posted": str(job.get("PublicationStartDate") or "")[:10], "description": description,
        }

    def search(self, keyword, place="", radius=None):
        """Openings for a title near a place (a city, or a whole state), most relevant first."""
        listings = []
        for page in range(1, self.MAX_PAGES + 1):
            params = {"Keyword": keyword, "ResultsPerPage": self.PAGE_SIZE, "Page": page, "Fields": "Full",
                      "WhoMayApply": "public", "DatePosted": self.DAYS}
            if place:
                params["LocationName"] = self.location_name(place)
                if radius:
                    params["Radius"] = int(radius)
            result = (self._get(params).get("SearchResult") or {})
            items = result.get("SearchResultItems") or []
            listings.extend(self._listing(item) for item in items)
            if len(items) < self.PAGE_SIZE:
                break
        return [listing for listing in listings if listing["guid"]]

    def status(self, url):
        return "Unknown"  # the API can't look up one announcement; never mark a real job closed by guessing


class Adzuna:
    """Adzuna (adzuna.com), a job search engine that gathers listings from many boards, through its official API.

    Free for personal use (api terms: "personal research"), but it needs your own free key: register at
    developer.adzuna.com for an app id and app key, then put ADZUNA_APP_ID and ADZUNA_APP_KEY in the .env file.
    Without them this site is skipped. The free limits are 25 requests a minute and 250 a day, so Job Finder reads
    one page of 50 per title and place, no faster than 20 a minute. Adzuna's terms ask that listings are labelled
    "Jobs by Adzuna": the source name and each saved description say so, with a link. The API gives only a short
    snippet of each description, and cannot look up one listing, so a saved job's status stays "Unknown".
    """
    name = "Adzuna"
    domain = "adzuna.com"
    API = "https://api.adzuna.com/v1/api/jobs/us/search/1"
    PAGE_SIZE = 50
    DELAY = 3.0  # seconds between requests (20 a minute; the limit is 25)
    DAYS = 30
    KM_PER_MILE = 1.609344

    def __init__(self, timeout=30):
        self.timeout = timeout
        self._last = 0.0
        self.requests = 0
        self.app_id = os.getenv("ADZUNA_APP_ID", "").strip()
        self.app_key = os.getenv("ADZUNA_APP_KEY", "").strip()

    @property
    def configured(self):
        return bool(self.app_id and self.app_key)

    setup_hint = "Add ADZUNA_APP_ID and ADZUNA_APP_KEY to .env (free keys: developer.adzuna.com)."

    def _get(self, params):
        pause = self.DELAY - (time.monotonic() - self._last)
        if pause > 0:
            time.sleep(pause)
        self._last = time.monotonic()
        self.requests += 1
        response = requests.get(self.API, params={**params, "app_id": self.app_id, "app_key": self.app_key},
                                timeout=self.timeout, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
        if response.status_code == 429:
            raise SiteBlocked(f"{self.name} answered HTTP 429 (its free limit was reached)")
        if response.status_code in (401, 403):
            raise ValueError(f"{self.name} refused the keys (HTTP {response.status_code}). "
                             "Check ADZUNA_APP_ID and ADZUNA_APP_KEY in .env.")
        if response.status_code != 200:
            raise ValueError(f"{self.name} answered HTTP {response.status_code}")
        return response.json()

    @staticmethod
    def stable_url(url):
        """The listing link without `se`, a code that changes with every search: the same ad must give the same link,
        or each search would save it again as a new job. (The link's `v` code is the same every time for an ad.)"""
        parsed = urlparse(str(url or ""))
        query = urlencode([(key, value) for key, value in parse_qsl(parsed.query) if key != "se"])
        return urlunparse(parsed._replace(query=query))

    @staticmethod
    def location_name(place):
        """"Baltimore, MD" -> "Baltimore, Maryland" (a state alone stays as it is)."""
        city, _, state = str(place).partition(",")
        full = STATE_NAMES.get(state.strip().upper())
        return f"{city.strip()}, {full}" if full else str(place).strip()

    def _listing(self, item):
        place = item.get("location") or {}
        area = [str(part) for part in (place.get("area") or [])]
        shown = str(place.get("display_name") or "").strip()
        remote = bool(re.search(r"\bremote\b", f"{item.get('title') or ''} {shown}", re.I))
        state = area[1] if len(area) > 1 else ""
        city = area[-1] if len(area) > 2 else shown.split(",")[0].strip()
        # Adzuna names a county or no state ("Edgewood, Harford County"); "Edgewood, MD" is what the location checks read.
        code = next((abbr for abbr, full in STATE_NAMES.items() if full.casefold() == state.casefold()), "")
        if code and city and area[0] == "US":
            shown = f"{city}, {code}"
        lat = lon = None
        try:
            lat, lon = float(item.get("latitude")), float(item.get("longitude"))
        except (TypeError, ValueError):
            pass
        text = html_lib.unescape(re.sub(r"<[^>]+>", "", str(item.get("description") or "")))
        parts = [re.sub(r"\s+", " ", text).strip()]
        if item.get("salary_min") and item.get("salary_max"):
            parts.append(f"Pay: ${float(item['salary_min']):,.0f} - ${float(item['salary_max']):,.0f} per year"
                         + (" (estimated by Adzuna)" if str(item.get("salary_is_predicted")) == "1" else ""))
        parts.append("Jobs by Adzuna (https://www.adzuna.com)")
        return {
            "guid": str(item.get("id") or "").strip(), "title": html_lib.unescape(str(item.get("title") or "")).strip(),
            "company": str((item.get("company") or {}).get("display_name") or "").strip(),
            "url": self.stable_url(str(item.get("redirect_url") or "").strip()),
            "location": "United States (Remote)" if remote else shown, "city": "" if remote else city,
            "state": "" if remote else state, "country": "United States", "lat": lat, "lon": lon, "miles": None,
            "posted": str(item.get("created") or "")[:10], "description": "\n\n".join(part for part in parts if part),
        }

    def search(self, keyword, place="", radius=None):
        """Openings for a title near a place (a city, or a whole state), most relevant first. One page only."""
        params = {"what": keyword, "results_per_page": self.PAGE_SIZE, "max_days_old": self.DAYS,
                  "content-type": "application/json"}
        if place:
            params["where"] = self.location_name(place)
            if radius:
                params["distance"] = max(1, round(int(radius) * self.KM_PER_MILE))  # the API counts kilometres
        items = self._get(params).get("results") or []
        return [listing for listing in (self._listing(item) for item in items) if listing["guid"] and listing["url"]]

    def status(self, url):
        return "Unknown"  # the API can't look up one listing; never mark a real job closed by guessing


JOB_SITES = [NationalLaborExchange, USAJobs, Adzuna]
JOB_SITE_NAMES = frozenset(site.name for site in JOB_SITES)


def job_site_for(source_type):
    return next((site for site in JOB_SITES if site.name == source_type), None)


def search_places(state_text, cities, state_names):
    """(place, radius) pairs to search: each city with its radius, each whole state picked, or the state box."""
    places = []
    for item in cities or []:
        if not isinstance(item, dict):
            continue
        city = str(item.get("city") or "").strip()
        if not city:
            continue
        if city.casefold() in state_names:
            places.append((city, None))
        else:
            try:
                radius = int(item.get("radius") or 25)
            except (TypeError, ValueError):
                radius = 25
            places.append((city, radius))
    if not places and state_text:
        places = [(part.strip(), None) for part in re.split(r"[,/;]", state_text) if part.strip()]
    return list(dict.fromkeys(places))
