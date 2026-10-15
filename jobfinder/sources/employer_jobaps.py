"""JobAps job portals (jobapscloud.com/<agency>), such as the State of Maryland's job openings (agency "MD").

The portal publishes its whole list as one RSS feed for job seekers (https://www.jobapscloud.com/MD/rss.asp), so a search
reads every opening with one request; a job's own page carries JobPosting data with the full announcement. Maryland lists
a county, not a town, for most jobs, so a job's town is read from its "Location of Position" address when that names a
town Job Finder knows, and is otherwise the county's seat.
"""

import html as html_lib
import json
import re
from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup

from jobfinder.profiles.travel import place_point
from jobfinder.sources.employer_common import HTML, _WholeBoard, _opening, _schedule

HOSTS = ("jobapscloud.com", "jobaps.com")
RSS = "application/rss+xml, application/xml, text/xml"
# The town to measure from for the counties Maryland's feed names (the county seat, or its biggest town).
COUNTY_SEATS = {
    "allegany": "Cumberland", "anne arundel": "Annapolis", "baltimore": "Baltimore", "baltimore city": "Baltimore",
    "baltimore county": "Towson", "calvert": "Prince Frederick", "caroline": "Denton", "carroll": "Westminster",
    "cecil": "Elkton", "charles": "La Plata", "dorchester": "Cambridge", "frederick": "Frederick", "garrett": "Oakland",
    "harford": "Bel Air", "howard": "Columbia", "kent": "Chestertown", "montgomery": "Rockville",
    "prince george's": "Upper Marlboro", "queen anne's": "Centreville", "somerset": "Princess Anne", "st. mary's": "Leonardtown",
    "talbot": "Easton", "washington": "Hagerstown", "washington county": "Hagerstown", "wicomico": "Salisbury",
    "worcester": "Snow Hill",
}
_ACRONYMS = {"IT", "HR", "II", "III", "IV", "MD", "GIS", "CAD", "EMS", "RN", "LPN", "CDL", "MBE", "DNR", "MVA", "PC", "AV", "ID"}
_NUMBER = re.compile(r"\s*-?\s*#\s*(\d{2})-(\d{6})-(\d{4})\s*$")
_UNIT = re.compile(r"\b(?:division|unit|office|bureau|section|branch|department|administration|team|center)\s*$", re.I)


def _tidy(text):
    """An all-capitals class title ("IT PROGRAMMER ANALYST") as words ("IT Programmer Analyst"); other text as it is."""
    text = " ".join(str(text or "").split())
    if not text.isupper():
        return text
    return re.sub(r"[A-Za-z][A-Za-z']*", lambda match: match.group(0) if match.group(0) in _ACRONYMS else match.group(0).capitalize(), text)


def working_title(raw):
    """The title to show and match. Maryland writes "CLASS TITLE (Working title)" or "Working title (CLASS TITLE)"; the
    working title says what the job is, so it is used on its own when there is one (not when the brackets only name the
    unit: "SYSTEMS CONTROL ACCOUNTANT SUPERVISOR (General Accounting Division)")."""
    text = re.sub(r"\*[^*]*\*", " ", _NUMBER.sub("", " ".join(str(raw or "").split())))  # no "*REPOST*" or job number
    text = " ".join(text.split())
    match = re.match(r"^(?P<first>.+?)\s*\((?P<second>[^()]*)\)$", text)
    if match:
        first, second = match.group("first").strip(), match.group("second").strip()
        if first.isupper() != second.isupper() and second and not _UNIT.search(second):
            return _tidy(second if first.isupper() else first)
        if first.isupper() and not second.isupper() and _UNIT.search(second):
            return _tidy(first)
    return _tidy(text)


def _date(text):
    """2026-10-14 from "10/14/2026 4:00:00 PM"; "" when there is no date."""
    match = re.search(r"(\d{1,2})/(\d{1,2})/(\d{4})", str(text or ""))
    return f"{match.group(3)}-{int(match.group(1)):02d}-{int(match.group(2)):02d}" if match else ""


def job_number(url_or_title):
    """26-004547-0006 from a job's address (R1=26&R2=004547&R3=0006) or from "... - #26-004547-0006"; "" if there is none."""
    text = str(url_or_title or "")
    match = _NUMBER.search(text)
    if match:
        return "-".join(match.groups())
    query = {key.casefold(): value[0] for key, value in parse_qs(urlparse(text).query).items()}
    if all(key in query for key in ("r1", "r2", "r3")):
        return f"{query['r1']}-{query['r2']}-{query['r3']}"
    return ""


def _label_values(fragment):
    """{"Department": "...", "Work Location": ["Harford", ...], ...} from the feed's description (bold label, then text or a list)."""
    found = {}
    for label in fragment.find_all("strong"):
        name = label.get_text(" ", strip=True).rstrip(":")
        items = label.find_next_sibling("ul")
        if items is not None:
            found[name] = [li.get_text(" ", strip=True) for li in items.find_all("li")]
        else:
            tail = label.next_sibling
            found[name] = " ".join(str(tail or "").split()).strip(" .")
    return found


def parse_feed(text):
    """Every opening in the portal's RSS feed: [{"id", "title", "url", "department", "salary", "kind", "opened", "deadline", "places"}]."""
    jobs = []
    for block in re.findall(r"<item>(.*?)</item>", text, re.S):
        def tag(name):
            found = re.search(rf"<{name}>(.*?)</{name}>", block, re.S)
            return html_lib.unescape(found.group(1)).strip() if found else ""

        url, title = tag("link"), tag("title")
        number = job_number(url) or job_number(title)
        if not (url and number):
            continue
        facts = _label_values(BeautifulSoup(tag("description"), "html.parser"))
        kinds = facts.get("Employment Type") or []
        jobs.append({"id": number, "title": working_title(title), "url": url, "department": str(facts.get("Department") or ""),
                     "salary": str(facts.get("Salary") or ""), "kind": kinds[0] if kinds else "",
                     "opened": str(facts.get("Date Opened") or ""), "deadline": str(facts.get("Filing Deadline") or ""),
                     "places": [place for place in facts.get("Work Location") or [] if place]})
    return jobs


class JobAps(_WholeBoard):
    """One JobAps portal. Settings: agency (the code in its address, "MD"), state ("MD": lets county names become towns)."""

    def __init__(self, config):
        super().__init__(config)
        self.agency = str(config["agency"])
        self.state = str(config.get("state") or "").upper()
        self.base = f"https://www.jobapscloud.com/{self.agency}"

    def owns(self, url):
        parsed = urlparse(url)
        host = (parsed.hostname or "").casefold()
        parts = [part for part in parsed.path.split("/") if part]
        return host.endswith(HOSTS) and bool(parts) and parts[0].casefold() == self.agency.casefold()

    def _id_from(self, url):
        return job_number(url)

    def _fetch(self, http):
        response = http.get(f"{self.base}/rss.asp", accept=RSS)
        if response.status_code != 200:
            raise ValueError(f"JobAps answered HTTP {response.status_code}")
        try:
            text = response.content.decode("utf-8")
        except UnicodeDecodeError:
            text = response.content.decode("latin-1")
        return parse_feed(text)

    def _first_place(self, job):
        return self._seat(job["places"][0]) if job["places"] else ""

    def _listing(self, job):
        return {"title": job["title"], "id": job["id"], "path": job["id"], "location": self._first_place(job), "raw": job}

    def status(self, url, http):
        number = self._id_from(url)
        if not number:
            return "Unknown"
        if self._all is None:
            self._all = [self._listing(job) for job in self._fetch(http)]
        if not self._all:
            return "Unknown"  # an empty feed is more likely a portal problem than every job closing at once
        return "Open" if any(listing["id"] == number for listing in self._all) else "Closed"

    def _seat(self, county):
        """"Bel Air, MD, US" for Harford; a place the portal names that is not a county is kept as written."""
        name = " ".join(str(county or "").split())
        town = COUNTY_SEATS.get(name.casefold()) if self.state == "MD" else None
        return f"{town or name}, {self.state}, US" if self.state else name

    def _address_town(self, soup):
        """The town in the job's "Location of Position" address ("... 201 W. Preston Street Baltimore, MD 21201"), when it is
        a town Job Finder knows; else ""."""
        heading = next((node for node in soup.find_all("h2") if "location of position" in node.get_text().casefold()), None)
        if heading is None or not self.state:
            return ""
        text = []
        for sibling in heading.next_siblings:
            if getattr(sibling, "name", None) == "h2":
                break
            text.append(sibling.get_text(" ", strip=True) if hasattr(sibling, "get_text") else str(sibling))
        match = re.search(r"([A-Za-z.' -]+?)\s*,\s*(?:" + re.escape(self.state) + r"|Maryland)\b", " ".join(text))
        words = match.group(1).split() if match else []
        for size in (3, 2, 1):  # the longest words that make a known town: "Prince Frederick" before "Frederick"
            if len(words) >= size:
                candidate = " ".join(words[-size:])
                if place_point(f"{candidate}, {self.state}", self.state):
                    return candidate
        return ""

    def detail(self, listing, http, employer):
        job = listing["raw"]
        response = http.get(job["url"], accept=HTML)
        if response.status_code != 200:
            return None
        soup = BeautifulSoup(response.text, "html.parser")
        posting = {}
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(script.string or "")
            except ValueError:
                continue
            for node in data if isinstance(data, list) else [data]:
                if isinstance(node, dict) and node.get("@type") == "JobPosting":
                    posting = node
        description = str(posting.get("description") or "").replace("\\r\\n", "<br>").replace("\\n", "<br>")
        facts = [f"<p>{part}</p>" for part in (job["department"] and f"Agency: {job['department']}",
                                                 job["salary"] and f"Salary: {job['salary']}",
                                                 job["deadline"] and f"Apply by: {job['deadline']}") if part]
        town = self._address_town(soup)
        places = ([f"{town}, {self.state}, US"] if town else []) + [self._seat(place) for place in job["places"]]
        opening = _opening(employer, title=job["title"], url=job["url"], locations=places or [listing["location"]],
                           html="".join(facts) + (description or ""), posted=str(posting.get("datePosted") or "")[:10] or _date(job["opened"]),
                           schedule=_schedule(job["kind"] or posting.get("employmentType")))
        opening["salary"] = job["salary"]
        return opening
