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
import re
import time

import requests

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


JOB_SITES = [NationalLaborExchange]
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
