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

from jobfinder import paths
from jobfinder.sources.job_listings import ANY_TITLE, extract_jobs, matching_title
from jobfinder.sources.remote_states import is_remote_place, place_states

from jobfinder.sources.employer_boards import Ashby, BambooHR, Greenhouse, Lever, Recruitee, Teamtailor
from jobfinder.sources.employer_common import HTML, USER_AGENT, Http, _arrangement, _opening, _plain, _schedule  # noqa: F401
from jobfinder.sources.employer_enterprise import ICIMS, Oracle, SuccessFactors, UltiPro, Workday
from jobfinder.sources.employer_more import ADP, NeoGov, Paylocity, SmartRecruiters, Workable
SOURCE_TYPE = "Employer careers"
CONFIG_FILE = paths.WATCHED_EMPLOYERS_FILE
# Boards found in web searches; managed by Job Finder and searched again on later runs.
DISCOVERED_FILE = paths.DISCOVERED_EMPLOYERS_FILE
MAX_DISCOVERED = 40
MIN_SEARCHES_BEFORE_DROP = 5  # a found board with no title match after this many searches is no longer searched
MAX_KEYWORDS = 16
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
    {"name": "Baltimore County Government", "system": "neogov", "agency": "baltimorecounty", "domain": "baltimorecountymd.gov"},
    {"name": "Howard County Government", "system": "neogov", "agency": "howardcountymd", "domain": "howardcountymd.gov"},
    {"name": "Anne Arundel County", "system": "neogov", "agency": "annearundel", "domain": "aacounty.org"},
    {"name": "Carroll County Government", "system": "neogov", "agency": "carrollcounty", "domain": "carrollcountymd.gov"},
    {"name": "State of Delaware", "system": "neogov", "agency": "delaware", "domain": "delaware.gov"},
    {"name": "Harford Community College", "system": "neogov", "agency": "harfordcc", "domain": "harford.edu"},
    {"name": "Community College of Baltimore County", "system": "neogov", "agency": "ccbcmd", "domain": "ccbc.edu"},
    {"name": "Howard Community College", "system": "neogov", "agency": "howardcc", "domain": "howardcc.edu"},
    {"name": "Baltimore City Community College", "system": "neogov", "agency": "bccc", "domain": "bccc.edu"},
    {"name": "Carroll Community College", "system": "neogov", "agency": "carrollcc", "domain": "carrollcc.edu"},
    {"name": "City of Baltimore", "system": "workday", "host": "baltimorecity.wd1.myworkdayjobs.com", "tenant": "baltimorecity", "site": "External", "domain": "baltimorecity.gov"},
    {"name": "Franklin Templeton", "system": "workday", "host": "franklintempleton.wd5.myworkdayjobs.com", "tenant": "franklintempleton", "site": "Primary-External-1", "domain": "franklintempleton.com"},
    {"name": "M&T Bank", "system": "workday", "host": "mtb.wd5.myworkdayjobs.com", "tenant": "mtb", "site": "MTB", "domain": "mtb.com"},
    {"name": "Loyola University Maryland", "system": "workday", "host": "loyola.wd5.myworkdayjobs.com", "tenant": "loyola", "site": "External", "domain": "loyola.edu"},
    {"name": "University of Baltimore", "system": "workday", "host": "marylandconnect.wd1.myworkdayjobs.com", "tenant": "marylandconnect", "site": "UBaltCareers", "domain": "ubalt.edu"},
    {"name": "University of Maryland, College Park", "system": "workday", "host": "umd.wd1.myworkdayjobs.com", "tenant": "umd", "site": "UMCP", "domain": "umd.edu"},
    {"name": "University of Maryland Global Campus", "system": "workday", "host": "umgc.wd1.myworkdayjobs.com", "tenant": "umgc", "site": "UMGC_Careers", "domain": "umgc.edu"},
    {"name": "University of Maryland, Baltimore", "system": "workday", "host": "umb.wd1.myworkdayjobs.com", "tenant": "umb", "site": "UMBExternal", "domain": "umaryland.edu"},
    {"name": "Stanley Black & Decker", "system": "workday", "host": "sbdinc.wd1.myworkdayjobs.com", "tenant": "sbdinc", "site": "Stanley_Black_Decker_Career_Site", "domain": "stanleyblackanddecker.com"},
    {"name": "Medifast", "system": "workday", "host": "medifastinc.wd108.myworkdayjobs.com", "tenant": "medifastinc", "site": "Medifast", "domain": "medifastinc.com"},
    # Employers with sites in Abingdon, Belcamp, Bel Air and Forest Hill (Harford County), checked October 2026.
    {"name": "Wegmans", "system": "workday", "host": "wegmans.wd1.myworkdayjobs.com", "tenant": "wegmans", "site": "Wegmans", "domain": "wegmans.com"},
    {"name": "Target", "system": "workday", "host": "target.wd5.myworkdayjobs.com", "tenant": "target", "site": "targetcareers", "domain": "target.com"},
    {"name": "Walmart", "system": "workday", "host": "walmart.wd504.myworkdayjobs.com", "tenant": "walmart", "site": "WalmartExternal", "domain": "walmart.com"},
    {"name": "Gap Inc. (Old Navy, Gap, Banana Republic, Athleta)", "system": "workday", "host": "gapinc.wd1.myworkdayjobs.com", "tenant": "gapinc", "site": "GAPINC", "domain": "gapinc.com"},
    {"name": "TJX (HomeGoods, TJ Maxx, Marshalls)", "system": "workday", "host": "tjx.wd1.myworkdayjobs.com", "tenant": "tjx", "site": "TJX_EXTERNAL", "domain": "tjx.com"},
    {"name": "Dollar Tree", "system": "workday", "host": "dollartree.wd5.myworkdayjobs.com", "tenant": "dollartree", "site": "dollartreeus", "domain": "dollartree.com"},
    {"name": "Kohl's", "system": "workday", "host": "kohls.wd504.myworkdayjobs.com", "tenant": "kohls", "site": "kohlscareers", "domain": "kohls.com"},
    {"name": "PNC", "system": "workday", "host": "pnc.wd5.myworkdayjobs.com", "tenant": "pnc", "site": "External", "domain": "pnc.com"},
    {"name": "At Home", "system": "oracle", "host": "hdiy.fa.us2.oraclecloud.com", "site": "CX", "domain": "athome.com"},
    {"name": "Booz Allen Hamilton", "system": "workday", "host": "bah.wd1.myworkdayjobs.com", "tenant": "bah", "site": "BAH_Jobs", "domain": "boozallen.com"},
    {"name": "Ingredion", "system": "workday", "host": "ingredion.wd1.myworkdayjobs.com", "tenant": "ingredion", "site": "IngredionCareers", "domain": "ingredion.com"},
    {"name": "Harford County Government", "system": "workday", "host": "harfordcountymd.wd503.myworkdayjobs.com", "tenant": "harfordcountymd", "site": "Harford_County_External_Career_Site", "domain": "harfordcountymd.gov"},
    {"name": "Long & Foster Real Estate", "system": "smartrecruiters", "company": "LongFosterRealEstate", "domain": "longandfoster.com"},
]



ADAPTERS = {"workday": Workday, "oracle": Oracle, "icims": ICIMS, "successfactors": SuccessFactors, "ultipro": UltiPro,
            "greenhouse": Greenhouse, "lever": Lever, "ashby": Ashby, "bamboohr": BambooHR, "smartrecruiters": SmartRecruiters,
            "adp": ADP, "paylocity": Paylocity, "workable": Workable, "neogov": NeoGov,
            "recruitee": Recruitee, "teamtailor": Teamtailor}


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
        if ANY_TITLE in wanted:
            keywords = [""]  # no job title: every opening on the board
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

    def remote_limits(self, url, http):
        """States a remote posting is limited to, when this system lists them per location; else an empty set."""
        check = getattr(self.adapter, "remote_limits", None)
        return check(url, http) if check else set()


_ID_FIELDS = ("system", "host", "tenant", "site", "board", "slug", "company", "eu", "cid", "ccId", "guid", "agency")


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
    found = [config for config in (_read_list(discovered_path) or []) if isinstance(config, dict)
             and not (config.get("searches", 0) >= MIN_SEARCHES_BEFORE_DROP and not config.get("matches"))]
    found.sort(key=lambda config: config.get("matches", 0), reverse=True)  # boards that matched your titles first
    configs = list(configs) + [dict(config, discovered=True) for config in found]
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
    stored.sort(key=lambda item: (item.get("matches", 0), item.get("last_seen", "")), reverse=True)
    temporary = Path(str(path) + ".tmp")
    try:
        temporary.write_text(json.dumps(stored[:cap], indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, path)
    except OSError:
        pass


def record_board_result(config, matches, path=DISCOVERED_FILE):
    """Count one search of a discovered board and how many of its openings matched, so useful boards rank first."""
    stored = [item for item in (_read_list(path) or []) if isinstance(item, dict)]
    key = config_key(config)
    for item in stored:
        if config_key(item) == key:
            item["searches"] = int(item.get("searches", 0)) + 1
            item["matches"] = int(item.get("matches", 0)) + int(matches)
            break
    else:
        return
    temporary = Path(str(path) + ".tmp")
    try:
        temporary.write_text(json.dumps(stored, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, path)
    except OSError:
        pass


def employer_for_url(url, employers):
    return next((employer for employer in employers if employer.adapter.owns(url)), None)
