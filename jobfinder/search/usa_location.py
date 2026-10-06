"""Is this posting in the United States, in which state, and is the work remote, hybrid or on-site?"""

import json
import re

from bs4 import BeautifulSoup

from jobfinder.search.fetching import get_domain
from jobfinder.sources.job_listings import arrangement_types
from jobfinder.sources.remote_states import restriction_states

US_STATES = {
    "alabama": "AL",
    "alaska": "AK",
    "arizona": "AZ",
    "arkansas": "AR",
    "california": "CA",
    "colorado": "CO",
    "connecticut": "CT",
    "delaware": "DE",
    "florida": "FL",
    "georgia": "GA",
    "hawaii": "HI",
    "idaho": "ID",
    "illinois": "IL",
    "indiana": "IN",
    "iowa": "IA",
    "kansas": "KS",
    "kentucky": "KY",
    "louisiana": "LA",
    "maine": "ME",
    "maryland": "MD",
    "massachusetts": "MA",
    "michigan": "MI",
    "minnesota": "MN",
    "mississippi": "MS",
    "missouri": "MO",
    "montana": "MT",
    "nebraska": "NE",
    "nevada": "NV",
    "new hampshire": "NH",
    "new jersey": "NJ",
    "new mexico": "NM",
    "new york": "NY",
    "north carolina": "NC",
    "north dakota": "ND",
    "ohio": "OH",
    "oklahoma": "OK",
    "oregon": "OR",
    "pennsylvania": "PA",
    "rhode island": "RI",
    "south carolina": "SC",
    "south dakota": "SD",
    "tennessee": "TN",
    "texas": "TX",
    "utah": "UT",
    "vermont": "VT",
    "virginia": "VA",
    "washington": "WA",
    "west virginia": "WV",
    "wisconsin": "WI",
    "wyoming": "WY",
}

US_STATE_ABBREVIATIONS = set(US_STATES.values())
# Abbreviations that are also credentials or words ("Jane Doe, MD"), so whole-page text needs more than a comma.
_AMBIGUOUS_ABBREVIATIONS = {"MD", "PA", "MA"}
_LOCATION_CUE = re.compile(r"(?:location|located|office|address|based in|campus|headquarter\w*)\b[^.]{0,45}$", re.I)


def has_location_cue(before):
    """True when the words just before a city read like a place ('Location: Towson', 'office in Towson')."""
    return bool(_LOCATION_CUE.search(str(before)[-90:]))


def find_state_from_text(text, strict=False):
    """The first U.S. state named in the text.

    strict is for whole-page text: an abbreviation such as MD then needs a ZIP code or wording
    like 'Location:' in front of the city, because 'Jane Doe, MD' is a doctor, not Maryland.
    """
    text_lower = text.lower()

    # "Washington, DC" is the District, not Washington State.
    text_lower = re.sub(r"\bwashington,?\s*d\.?\s?c\b\.?", "district of columbia", text_lower)
    if re.search(r"\bdistrict of columbia\b", text_lower) and not re.search(
            r"\b(?:" + "|".join(re.escape(name) for name in US_STATES) + r")\b",
            text_lower[:text_lower.index("district of columbia")]):
        return "DC"

    # Use the first state mentioned; longer names are tried first at each position
    # so "West Virginia" is not read as "Virginia".
    names = "|".join(re.escape(name) for name in sorted(US_STATES, key=len, reverse=True))
    match = re.search(rf"\b(?:{names})\b", text_lower)
    if match:
        return US_STATES[match.group(0)]

    # Strong abbreviation contexts such as "Gaithersburg, MD" or "MD 20877".
    abbreviations = "|".join(sorted(US_STATE_ABBREVIATIONS))
    abbreviation_pattern = (
        rf",\s*({abbreviations})\b"
        rf"|\b({abbreviations})\s+\d{{5}}(?:-\d{{4}})?\b"
    )

    for match in re.finditer(abbreviation_pattern, text):
        code = match.group(1) or match.group(2)
        if (strict and match.group(1) and code in _AMBIGUOUS_ABBREVIATIONS
                and not has_location_cue(text[:match.start()]) and not re.match(r"\s+\d{5}\b", text[match.end():])):
            continue
        return code

    return None


# Phrases that tie a remote job to where the applicant lives.
_RESIDENCY_TRIGGER = re.compile(
    r"(?:must|need to|required to|have to|should)\s+(?:currently\s+)?(?:reside|live|be\s+(?:located|based|a\s+resident))"
    r"|residents?\s+of|resident\s+in|(?:candidates|applicants)\s+(?:located\s+)?(?:in|from)\s+[A-Z]"
    r"|open\s+only\s+to|only\s+(?:open|available)\s+to|based\s+in\s+[A-Z][A-Za-z ]{2,20}\s+only"
    r"|residents?\s+only|out[- ]of[- ]state|in[- ]state\s+(?:candidates|residents|only)",
    re.I,
)

_OUT_OF_STATE = re.compile(r"out[- ]of[- ]state|in[- ]state\s+(?:candidates|residents|only)", re.I)
_STATE_NAMES = re.compile(r"\b(?:" + "|".join(re.escape(name) for name in sorted(US_STATES, key=len, reverse=True)) + r")\b", re.I)
# Abbreviations that are also ordinary words (IN, OR, ME...) only count right after a comma or bracket.
_WORD_LIKE_STATES = {"IN", "OR", "ME", "HI", "OK", "OH"}

_STATE_ABBREVIATION = re.compile(
    r"[,(]\s*(" + "|".join(sorted(US_STATE_ABBREVIATIONS)) + r")\b"
    r"|\b(" + "|".join(sorted(US_STATE_ABBREVIATIONS - _WORD_LIKE_STATES)) + r")\b")


def remote_state_restrictions(text, job_state=None, places=()):
    """States a remote job says its applicant must live in, or an empty set if it names none.

    "Cannot be out of state" with no state named means the job's own state.
    """
    text = str(text or "")[:8000]
    restricted = set()
    for trigger in _RESIDENCY_TRIGGER.finditer(text):
        window = text[trigger.start(): trigger.end() + 90]
        named = {US_STATES[match.group(0).casefold()] for match in _STATE_NAMES.finditer(window)}
        named |= {match.group(1) or match.group(2) for match in _STATE_ABBREVIATION.finditer(window)}
        if named:
            restricted |= named
        elif job_state and _OUT_OF_STATE.search(window):
            restricted.add(job_state)
    # Location strings such as "Work At Home-Florida" and lists such as "open in the following states: ..."
    return restricted | restriction_states(places, text)


def selected_state_codes(state_text, cities, statewide_states):
    """Every state the user selected, from the state box, statewide picks and city names."""
    codes = set(statewide_states)
    parts = re.split(r"[,/;]", state_text or "")
    for item in cities or []:
        if isinstance(item, dict):
            parts.append(str(item.get("city", "")).split(",")[-1])
    for part in parts:
        part = part.strip()
        if part.casefold() in US_STATES:
            codes.add(US_STATES[part.casefold()])
        elif part.upper() in US_STATE_ABBREVIATIONS:
            codes.add(part.upper())
    return codes


def iter_json_ld_objects(data):
    if isinstance(data, list):
        for item in data:
            yield from iter_json_ld_objects(item)
        return

    if not isinstance(data, dict):
        return

    yield data

    for value in data.values():
        if isinstance(value, (dict, list)):
            yield from iter_json_ld_objects(value)


def inspect_json_ld(soup):
    score = 0
    state = None
    evidence = []

    for script in soup.find_all("script", type="application/ld+json"):
        if not script.string:
            continue

        try:
            data = json.loads(script.string)
        except json.JSONDecodeError:
            continue

        for obj in iter_json_ld_objects(data):
            address = obj.get("address")
            if isinstance(address, dict):
                country = str(address.get("addressCountry", "")).strip().lower()
                region = str(address.get("addressRegion", "")).strip().upper()

                if country in {"us", "usa", "united states", "united states of america"}:
                    score += 5
                    evidence.append("structured addressCountry=US")

                if region in US_STATE_ABBREVIATIONS:
                    state = region
                    score += 3
                    evidence.append(f"structured addressRegion={region}")

            obj_type = obj.get("@type")
            types = set(obj_type if isinstance(obj_type, list) else [obj_type])
            if "JobPosting" in types:
                job_location = obj.get("jobLocation")
                raw = json.dumps(job_location or obj).lower()
                if "united states" in raw or '"us"' in raw or '"usa"' in raw:
                    score += 4
                    evidence.append("JobPosting location indicates US")

    return score, state, evidence


_PAGE_NOISE_ELEMENTS = ["select", "option", "datalist", "footer", "nav", "aside"]
_PAGE_NOISE_NAMES = re.compile(r"related|similar|recommended|sidebar|cookie|consent|breadcrumb|newsletter|footer|more[-_ ]jobs|other[-_ ]jobs", re.I)

# Wording that names a state without saying where the job is.
_STATE_BOILERPLATE = re.compile(
    r"\b[Aa]n?\s+[A-Z][a-z]+(?:\s[A-Z][a-z]+)?\s+(?i:corporation|company|limited liability company|llc|nonprofit|non-profit|partnership)"
    r"|(?i:incorporated|organized|registered|chartered|headquartered|domiciled)\s+(?i:in|under the laws of)\s+(?i:the\s+state\s+of\s+)?[A-Z][a-z]+(?:\s[A-Z][a-z]+)?"
    r"|\b[A-Z][a-z]+(?:\s[A-Z][a-z]+)?\s+(?:Consumer\s+Privacy|Privacy|Fair\s+Chance|Fair\s+Employment|Pay\s+Transparency|Equal\s+Pay|Paid\s+Sick|Human\s+Rights|Civil\s+Rights|Workers'?\s+Compensation)\b"
    r"|\b(?:Washington|New\s+York)\s+(?:Post|Times|Examiner|Mutual|Life|Yankees|Mets|Giants|Jets|Knicks|Rangers|Nationals|Capitals|Wizards|Commanders|Magazine)\b"
    r"|\bIndiana\s+Jones\b|\bVirginia\s+Woolf\b|\bKansas\s+City\b"
    # A list of the company's offices says nothing about where this job is.
    r"|\b(?i:offices)\b\s*(?i:in|include|located in|:)?\s*[^.]{0,120}")


def page_body_text(soup):
    """Visible text of the main content: no dropdowns, menus, footers, sidebars or 'related jobs' lists."""
    doomed = list(soup(_PAGE_NOISE_ELEMENTS))
    doomed += soup.find_all(class_=_PAGE_NOISE_NAMES) + soup.find_all(id=_PAGE_NOISE_NAMES)
    for tag in doomed:
        if not getattr(tag, "decomposed", False):
            tag.decompose()
    return soup.get_text(" ", strip=True)


def analyze_usa_location(html, extra_text="", source_label="page", page_url=""):
    soup = BeautifulSoup(html, "html.parser")
    # The listing's own location is trusted; the rest of the page is searched with the noise taken out.
    location_text = str(extra_text or "")
    body = _STATE_BOILERPLATE.sub(" ", page_body_text(soup))
    combined = f"{location_text} {body}".strip()
    lower = combined.lower()
    evidence = []
    country = None
    state = None
    score = 0

    # Count a country mention once, even when the page says US, USA and United States.
    # The listing's own location often ends in ", US" (capitals only, so the word "us" never counts).
    listing_says_us = bool(re.search(r"(?<![A-Za-z])U\.?S\.?A?(?![A-Za-z])", location_text))
    if ("united states" in lower or re.search(r"\busa\b|\bu\.s\.a?\.?\b", lower) or listing_says_us):
        score += 4
        country = "United States"
        evidence.append(f"{source_label}: U.S. country mention (+4)")

    state = find_state_from_text(location_text) or find_state_from_text(body, strict=True)
    if state:
        score += 3
        country = "United States"
        evidence.append(f"{source_label}: state {state} (+3)")
    elif any(re.search(pattern, lower) for pattern in (
        r"remote\s*[-–—,/|]?\s*(?:us|usa|united states)",
        r"(?:us|usa|united states)\s*[-–—,/|]?\s*remote",
    )):
        state = "US Remote"
        country = "United States"
        score += 2
        evidence.append(f"{source_label}: U.S. remote role (+2)")

    # A ZIP code counts only next to a state or the word ZIP; a salary like $90000 is not one.
    if re.search(r"\b(?:" + "|".join(sorted(US_STATE_ABBREVIATIONS)) + r")\s+\d{5}(?:-\d{4})?\b|(?i:\bzip(?: code)?)\s*:?\s*\d{5}\b", combined):
        score += 1
        evidence.append(f"{source_label}: ZIP pattern (+1)")

    # An address on an official .edu page is stronger evidence than a country word alone.
    page_domain = get_domain(page_url) if page_url else ""
    if page_domain.endswith(".edu"):
        state_codes = "|".join(sorted(US_STATE_ABBREVIATIONS))
        address = re.search(
            rf"\b[A-Za-z][A-Za-z .'-]{{1,45}},\s*({state_codes})\s+\d{{5}}(?:-\d{{4}})?\b",
            combined,
        )
        if address:
            score = max(score, 8)
            state = address.group(1)
            country = "United States"
            evidence.append(f"{source_label}: .edu page with U.S. campus address (at least 8)")

    json_score, json_state, json_evidence = inspect_json_ld(soup)
    if json_score:
        score += 2
        evidence.extend(json_evidence)
    if json_state:
        state = json_state
        country = "United States"

    score = min(score, 10)
    if score > 0 and country is None:
        country = "Possible United States"
    return {"country": country, "state": state, "score": score, "evidence": evidence}


def merge_location_data(base, incoming):
    if incoming["score"] > base["score"]:
        base["country"] = incoming["country"] or base["country"]

    if incoming.get("state") and not base.get("state"):
        base["state"] = incoming["state"]

    if incoming.get("country") == "United States":
        base["country"] = "United States"

    base["score"] = min(10, max(base["score"], incoming["score"]))
    base.setdefault("evidence", []).extend(incoming.get("evidence", []))
    return base


def detect_work_arrangement(job_title="", html=""):
    """Classify explicit job arrangements without guessing from site boilerplate.

    'Not remote' is Onsite, 'remote sensing' and 'remote work stipend' are not Remote, and an
    in-person interview does not make a job Onsite (see job_listings.arrangement_types).
    """
    def classify(text):
        found = arrangement_types(text)
        return next(iter(found)) if len(found) == 1 else None

    if job_title:
        title_types = arrangement_types(job_title)
        if len(title_types) > 1:
            return None
        if title_types:
            return next(iter(title_types))
    if not html:
        return None
    soup = BeautifulSoup(html, "html.parser")
    for script in soup.find_all("script", type="application/ld+json"):
        if script.string and re.search(r'"jobLocationType"\s*:\s*"TELECOMMUTE"', script.string, re.I):
            return "Remote"
    text = soup.get_text(" ", strip=True)
    context = re.findall(
        r"\b(?:work (?:arrangement|location|model)|workplace|location type|this (?:role|position|job) is)\s*[:\-]?\s*([^.;]{0,75})",
        text, re.I,
    )
    return classify(" ".join(context))


# Metropolitan areas a posting may name instead of a city: (what to look for, the city and state to use).
METRO_AREAS = (
    (r"dallas[-/ ]+fort worth|\bdfw\b", "Dallas, TX"), (r"houston[- ]the woodlands|greater houston", "Houston, TX"),
    (r"washington[ ,]+d\.?c\.?(?: metro| area| metropolitan)|\bdmv\b|washington metro", "Washington, DC"),
    (r"baltimore[-/ ]+washington|greater baltimore|baltimore metro", "Baltimore, MD"),
    (r"(?:san francisco )?bay area|silicon valley", "San Francisco, CA"), (r"greater los angeles|los angeles metro|\bsocal\b", "Los Angeles, CA"),
    (r"greater boston|boston metro", "Boston, MA"), (r"new york city metro|nyc metro|tri-state area", "New York, NY"),
    (r"greater chicago|chicagoland", "Chicago, IL"), (r"greater philadelphia|philadelphia metro|delaware valley", "Philadelphia, PA"),
    (r"greater atlanta|atlanta metro", "Atlanta, GA"), (r"research triangle|raleigh[-/ ]+durham", "Raleigh, NC"),
    (r"twin cities", "Minneapolis, MN"), (r"phoenix metro|greater phoenix", "Phoenix, AZ"), (r"greater seattle|seattle metro", "Seattle, WA"),
)
_STATE_CODES = "|".join(sorted(US_STATE_ABBREVIATIONS))
_CITY = r"[A-Z][A-Za-z.'’]+(?:[-/ ][A-Z][A-Za-z.'’]+){0,4}"
# "Role is full time in Irving-Las Colinas, TX", "Location: Austin, TX", "based in Denver, CO", "office in Reston, VA"
_PLACE_AFTER_CUE = re.compile(
    rf"(?i:\b(?:role|position|job|work|office|located|location|based|headquartered|onsite|on-site|hybrid|full[- ]time|part[- ]time)\b[^.\n]{{0,30}}?(?:\bin\b|:|-|–)\s*)"
    rf"({_CITY}),\s*({_STATE_CODES})\b")


def location_from_description(text):
    """The place a posting says its job is in, taken from its description when the site gave none: a "City, ST" next to words
    like "role is full time in" or "location:", or a named metropolitan area ("Dallas-Fort Worth Metroplex"). "" when unsure."""
    text = " ".join(str(text or "").split())
    if not text:
        return ""
    match = _PLACE_AFTER_CUE.search(text)
    if match:
        return f"{match.group(1)}, {match.group(2)}"
    lower = text.lower()
    for pattern, place in METRO_AREAS:
        if re.search(pattern, lower):
            return place
    return ""


_HYBRID_WORDS = re.compile(
    r"\bhybrid\b"
    r"|\b(?:one|two|three|four|[1-4])\s+days?\s+(?:a|per|each|every)\s+week\s+(?:in|at|from)\s+(?:the|our)\s+(?:office|workplace)"
    r"|\b(?:in|at|from)\s+(?:the|our)\s+office\s+(?:one|two|three|four|[1-4])\s+days?\b"
    r"|\bwork\s+(?:in|from)\s+(?:the|our)\s+office\s+(?:one|two|three|four|[1-4])\s+days?\b", re.I)
_REMOTE_WORDS = re.compile(r"\b(?:fully|completely|100%)\s+remote\b|\bthis\s+is\s+a\s+remote\s+(?:position|role|job)\b|\bwork\s+from\s+anywhere\b", re.I)
_ONSITE_WORDS = re.compile(
    r"\bon[- ]?site\b(?!\s+(?:interview|visit))|\bin[- ]office\s+(?:role|position|job)\b|\b(?:five|5)\s+days\s+(?:a|per)\s+week\s+in\s+(?:the|our)\s+office\b"
    r"|\bmust\s+(?:work|be)\s+(?:in|at|on)\s+(?:the|our)\s+office\b|\b(?:role|position|job)\s+is\s+(?:full|part)[- ]time\s+in\s+[A-Z]", re.I)


def arrangement_from_description(text):
    """'Hybrid', 'Remote' or 'Onsite' when the description says so plainly (e.g. "in our office 4 days a week" is Hybrid), else None."""
    text = " ".join(str(text or "").split())
    if _HYBRID_WORDS.search(text):
        return "Hybrid"
    remote, onsite = bool(_REMOTE_WORDS.search(text)), bool(_ONSITE_WORDS.search(text))
    if remote and onsite:
        return None
    return "Remote" if remote else "Onsite" if onsite else None
