"""What a listing asks for beyond software skills (degrees, years of experience, industry background), and which of
those the profile doesn't show. Top 10 Picks uses this to move listings you likely don't qualify for down the list.

A degree with an alternative ("Bachelor's degree or 4 years of experience", "or equivalent experience") counts as met
when the work history covers enough years. A degree with no alternative, in a field the profile doesn't show, is a hard
gap: the listing is likely not a fit at all."""

import json
import re
from datetime import date

from bs4 import BeautifulSoup

DEGREE_WORDS = (r"(?:bachelor'?s?|baccalaureate|b\.?f\.?a\.?|b\.?a\.?|b\.?s\.?|associate'?s?|master'?s?|m\.?f\.?a\.?|"
                r"degree|diploma)")
DEGREE_FIELD = re.compile(r"(?<![\w.])" + DEGREE_WORDS + r"(?:\s+degree)?(?:\s+or\s+higher)?\s+(?:in|from)\s+(?:an?\s+)?"
                          r"(?:accredited\s+)?(?:\d-year\s+)?([a-z][a-z &/,\-]{2,80})", re.I)
# Bump when the reading changes so saved results are read again.
VERSION = 12
# Areas of work a listing can be built around. When a listing keeps coming back to one (3+ mentions) and the profile
# never mentions it, the job likely wants background you don't show ("brand" all through a brand designer listing).
FOCUS_AREAS = {
    "brand": r"brand(?:ing|s|ed)?", "fashion": r"fashion|apparel|garments?|footwear|textiles?", "enterprise": r"enterprise",
    "B2B / SaaS": r"b2b|saas", "healthcare": r"health\s?care|clinical|patients?|medical", "finance": r"financial|fintech|banking",
    "retail": r"retail|merchandis\w+", "packaging": r"packaging", "motion": r"motion\s+(?:design|graphics)|animation",
    "video": r"video(?:graphy)?|film", "3D": r"3d|three-dimensional", "UX research": r"user\s+research|ux\s+research",
    "advertising": r"advertising|ad\s+campaigns?|ad\s+agency|creative\s+agency", "editorial": r"editorial|publications?|journalism",
    "e-commerce": r"e-?commerce", "gaming": r"gaming|video\s+games?", "cybersecurity": r"cyber\s?security|security\s+teams?",
    "engineering": r"civil\s+engineering|sewer|highway|structural", "teaching": r"teaching|faculty|students?|curriculum",
    "industrial design": r"industrial\s+design|product\s+development|factory|factories|sourcing",
    # Writing as the work itself, not "write clean code" or "written communication".
    "writing": r"copywrit\w*|ux\s+writ\w*|content\s+writ\w*|technical\s+writ\w*|writ(?:e|ing)\s+(?:and|&)\s+(?:edit|shape)\w*|"
               r"content\s+strateg\w*|microcopy|voice\s+and\s+tone|tone[\s-]+of[\s-]+voice|messaging|plain\s+language|"
               r"editorial\s+style|writers?|writing(?!\s+(?:code|tests|clean|software|unit|scripts|queries))",
}
FOREIGN = re.compile(r"\b(?:outside\s+(?:of\s+)?the\s+(?:united\s+states|u\.?s\.?)|foreign|evaluat\w+|equivalency)\b", re.I)
FASHION_SCHOOL = re.compile(r"\b(fashion|design|art|culinary|architecture|nursing|law|medical)\s+school\b", re.I)
DEGREE_ANY = re.compile(r"\b" + DEGREE_WORDS + r"\b", re.I)
YEARS = re.compile(r"(\d{1,2})\s*\+?\s*(?:(?:-|–|to)\s*\d{1,2}\s*)?\+?\s*(?:years?|yrs?)\b", re.I)
EQUIVALENT = re.compile(r"\bor\s+(?:the\s+)?(?:an?\s+)?equivalent\b|\bequivalent\s+(?:combination|work|professional|"
                        r"practical)\b|\bin\s+lieu\s+of\b|\bor\s+\d{1,2}\s*\+?\s*(?:years?|yrs?)\b", re.I)
PREFERRED = re.compile(r"\b(prefer(?:red|ably)?|nice to have|a plus|bonus|ideal(?:ly)?|desired)\b", re.I)
# "5+ years of brand design experience" / "3 years of experience in fashion"
YEARS_IN = re.compile(r"(?:years?|yrs?)\s+(?:of\s+)?((?:[a-z/&\-]+\s+){0,6}?)(?:experience|exp\b)(?:\s+(?:in|with|within)\s+"
                      r"(?:an?\s+|the\s+)?((?:[a-z/&\-]+\s*){1,4}))?", re.I)
FIELD_CUTS = re.compile(r"\s*(?:,?\s+or\s+(?:a\s+)?(?:related|similar|equivalent|comparable)|\s+(?:and|with|from|is|are|"
                        r"required|preferred|plus|strongly)\b|[.;:()•\n]).*$", re.I)
CLEARANCE = re.compile(r"\b(ts\s*/\s*sci|top\s+secret|secret|public\s+trust|dod|doe\s+[lq]|security)\s+(?:level\s+)?"
                       r"(?:security\s+)?clearance\b|\bclearance\s*(?:\(|:|-)?\s*(ts\s*/\s*sci|top\s+secret|secret)\b|"
                       r"\b(ts\s*/\s*sci)\b|\bactive\s+(clearance)\b", re.I)
CLEARANCE_OBTAIN = re.compile(r"\b(?:able|ability|eligib\w*|willing\w*)\s+(?:to\s+)?(?:obtain|get|receive|be\s+granted)|"
                              r"\beligib\w*\s+for\b|\bobtain(?:ed)?\s+(?:and\s+maintain\s+)?(?:a|an)?\b", re.I)
GENERIC = {"a", "an", "the", "and", "or", "of", "in", "with", "related", "relevant", "similar", "equivalent", "field",
           "fields", "area", "areas", "discipline", "professional", "industry", "work", "working", "hands-on", "hands",
           "on", "proven", "demonstrated", "previous", "prior", "direct", "progressive", "practical", "solid", "strong",
           "minimum", "least", "at", "total", "overall", "full-time", "paid", "applicable", "comparable", "combined",
           "experience", "years", "year", "plus", "other", "any", "all", "some", "this", "that", "position", "role",
           "similar", "s", "accredited", "university", "college", "school", "institution", "program", "study",
           "studies", "major", "concentration", "degree", "bachelor", "bachelors", "associate", "master", "masters"}


def _posting_descriptions(soup):
    """Job descriptions from JSON-LD JobPosting data: pages built with JavaScript (Ashby, Greenhouse boards) carry the
    listing there even when the visible page is nearly empty."""
    found = []

    def walk(item):
        if isinstance(item, list):
            for part in item:
                walk(part)
        elif isinstance(item, dict):
            kinds = item.get("@type")
            if "JobPosting" in (kinds if isinstance(kinds, list) else [kinds]) and item.get("description"):
                found.append(BeautifulSoup(str(item["description"]), "html.parser").get_text("\n", strip=True))
            walk(item.get("@graph"))

    for script in soup.find_all("script", type="application/ld+json"):
        try:
            walk(json.loads(script.string or ""))
        except ValueError:
            continue
    return found


def page_text(html):
    soup = BeautifulSoup(html or "", "html.parser")
    postings = _posting_descriptions(soup)
    for element in soup(["script", "style", "nav", "footer", "header"]):
        element.decompose()
    return "\n".join(postings + [soup.get_text("\n", strip=True)])


def _sentences(text):
    return [part.strip() for part in re.split(r"(?<=[.!?;])\s+|\n+|•", text or "") if part.strip()]


def _words(text):
    return [word for word in re.findall(r"[a-z][a-z\-]+", (text or "").lower()) if word not in GENERIC and len(word) > 2]


def _field(raw):
    return re.sub(r"\s+(?:program|major|degree)$", "", FIELD_CUTS.sub("", raw or "").strip(" ,-/"), flags=re.I)


def listing_requirements(text):
    """{"degrees": [{"fields": [...], "level": str, "alternative": bool}], "years": int|None,
    "experience": [{"years": int, "area": str}]} from the listing text. Preferred / nice-to-have lines are left out."""
    degrees, experience, years, clearance = [], [], None, None
    for sentence in _sentences(text):
        if PREFERRED.search(sentence):
            continue
        found = CLEARANCE.search(sentence)
        if found:
            level = next(group for group in found.groups() if group)
            level = re.sub(r"\s+", " ", level).upper().replace(" / ", "/") if level.lower() != "clearance" else ""
            level = {"SECURITY": "", "TOP SECRET": "Top Secret", "SECRET": "Secret", "PUBLIC TRUST": "Public Trust"}.get(level, level)
            obtainable = bool(CLEARANCE_OBTAIN.search(sentence)) and not re.search(r"\bactive\b|\bcurrent\b", sentence, re.I)
            if clearance is None or (clearance["obtainable"] and not obtainable):
                clearance = {"level": level, "obtainable": obtainable}
        if FOREIGN.search(sentence) and DEGREE_ANY.search(sentence):
            continue  # notes about how foreign degrees are evaluated, not a requirement
        alternative = bool(EQUIVALENT.search(sentence))
        found_degree = False
        for match in DEGREE_FIELD.finditer(sentence):
            if re.match(r"[A-Z][a-z]+(?:\s[A-Z][a-z]+)?,?\s+(?:[A-Z]{2}\b|Maryland|Virginia|Delaware|DC)", match.group(1)):
                continue  # "...in Baltimore, MD": a place, not a field of study
            fields =[_field(part) for part in re.split(r",|\s+or\s+|/", _field(match.group(1)))]
            # "...in Baltimore, MD" is a place, not a field of study.
            fields = [field for field in fields if _words(field)
                      and not re.search(re.escape(field) + r",?\s+(?:[A-Z]{2}\b|Maryland|Virginia|Delaware|DC)", text or "")]
            if fields:
                degrees.append({"fields": fields, "level": match.group(0).split()[0].lower(), "alternative": alternative})
                found_degree = True
        school = FASHION_SCHOOL.search(sentence)
        if school and not found_degree:
            degrees.append({"fields": [school.group(1).lower()], "level": "school", "alternative": alternative})
            found_degree = True
        if not found_degree and DEGREE_ANY.search(sentence) and re.search(r"\brequired|\bmust\b|\bminimum\b", sentence, re.I):
            degrees.append({"fields": [], "level": "degree", "alternative": alternative})
        if found_degree and alternative:
            continue  # "degree or 4 years of experience": the years are the degree's alternative, not a separate ask
        for match in YEARS.finditer(sentence):
            number = int(match.group(1))
            if not 1 <= number <= 25:
                continue
            tail = sentence[match.end():match.end() + 80]
            if not re.search(r"\bexp", tail, re.I):
                continue
            years = max(years or 0, number)
            area = YEARS_IN.search(sentence[match.start():])
            if area:
                words = _words((area.group(1) or "") + " " + (area.group(2) or ""))
                if words:
                    experience.append({"years": number, "area": " ".join(words)})
    focus = {}
    for area, pattern in FOCUS_AREAS.items():
        count = len(re.findall(r"\b(?:" + pattern + r")\b", text or "", re.I))
        if count >= 5:  # a few passing mentions ("our brand") aren't what the job is about
            focus[area] = count
    return {"degrees": degrees, "years": years, "experience": experience, "clearance": clearance, "focus": focus,
            "text": (text or "")[:20000], "version": VERSION}


def _history_years(work_history):
    """Years the work history covers (overlapping jobs counted once), from dates like "2019 – Present"."""
    covered = set()
    this_year = date.today().year
    for job in work_history or []:
        dates = str(job.get("dates") or "")
        found = [int(year) for year in re.findall(r"\b(19[6-9]\d|20[0-4]\d)\b", dates)]
        if not found:
            continue
        end = this_year if re.search(r"present|current|now", dates, re.I) else max(found)
        covered.update(range(min(found), max(end, min(found) + 1)))
    return len(covered)


def profile_text(profile):
    parts = [profile.get("primary_job_title") or ""] + list(profile.get("job_titles") or []) + list(profile.get("skills") or [])
    for school in profile.get("education") or []:
        parts += [str(value) for value in (school.values() if isinstance(school, dict) else [school])]
    for job in profile.get("work_history") or []:
        parts += [str(job.get(key) or "") for key in ("role", "company", "description")]
    return " ".join(parts).lower()


def _shows(field, known):
    words = _words(field)
    return bool(words) and all(re.search(r"\b" + re.escape(word.rstrip("s")), known) for word in words)


def requirement_gaps(requirements, profile):
    """[{"text": str, "hard": bool}] for what the listing asks and the profile doesn't show. Hard gaps (a required
    degree with no alternative) mean the listing is likely not a fit."""
    requirements = requirements or {}
    known = profile_text(profile)
    has_degree = bool(profile.get("education"))
    years = _history_years(profile.get("work_history"))
    gaps = []
    for degree in requirements.get("degrees") or []:
        fields = degree.get("fields") or []
        if fields and any(_shows(field, known) for field in fields):
            continue
        if not fields and has_degree:
            continue
        if degree.get("alternative") and years >= max(4, requirements.get("years") or 0):
            continue  # "or equivalent experience": the work history counts instead
        what = f"a degree in {' or '.join(field.title() for field in fields)}" if fields else "a degree"
        if degree.get("level") == "school":
            what = f"{fields[0]} school"
        gaps.append({"text": f"Asks for {what}" + (" (or equivalent experience)" if degree.get("alternative") else ""),
                     "hard": not degree.get("alternative")})
    clearance = requirements.get("clearance")
    if clearance and not re.search(r"\bclearance\b", known):
        what = f"a {clearance['level']} clearance" if clearance.get("level") else "a security clearance"
        if clearance.get("obtainable"):
            gaps.append({"text": f"Must be able to get {what}", "hard": False})
        else:
            gaps.append({"text": f"Requires an active {what[2:] if what.startswith('a ') else what}", "hard": True})
    # "Worked with copywriters" is working alongside writers, not writing.
    own_work = re.sub(r"\b(?:copy)?writers\b", " ", known)
    for area, count in sorted((requirements.get("focus") or {}).items(), key=lambda item: -item[1])[:2]:
        if not re.search(r"\b(?:" + FOCUS_AREAS.get(area, re.escape(area)) + r")\b", own_work, re.I):
            # Ten or more mentions: the job is centred on it (a footwear designer listing says "footwear" all through).
            gaps.append({"text": f"Built around {area} work (mentioned {count} times); your profile doesn't mention it",
                         "hard": False, "weight": 40 if count >= 10 else 15})
    seen = {gap["text"] for gap in gaps}
    text = requirements.get("text") or ""
    for term in profile.get("avoid_terms") or []:
        pattern = r"(?<![\w])" + r"\s+".join(re.escape(word) for word in str(term).split()) + r"(?![\w])"
        if term and re.search(pattern, text, re.I):
            gaps.append({"text": f"Mentions {term}, which you avoid", "hard": False, "avoid": True})
    gaps = [gap for n, gap in enumerate(gaps) if gap["text"] not in {g["text"] for g in gaps[:n]}]
    for item in requirements.get("experience") or []:
        area = item.get("area") or ""
        if area in seen or _shows(area, known):
            continue
        seen.add(area)
        gaps.append({"text": f"Asks for {item.get('years')}+ years of {area} experience", "hard": False})
    wanted = requirements.get("years") or 0
    if wanted and years and years < wanted:
        gaps.append({"text": f"Asks for {wanted}+ years of experience (your work history covers {years})", "hard": False})
    return gaps


REQUIRED_HEADING = re.compile(r"\b(requirements?|required|qualifications?|must[\s-]haves?|what you(?:'ll)? (?:need|bring)|"
                              r"you (?:have|bring|are)|who you are|skills|minimum|basic)\b", re.I)
PREFERRED_HEADING = re.compile(r"\b(preferred|nice[\s-]to[\s-]haves?|bonus|pluses|a plus|extra credit|desired|ideal)\b", re.I)
WEIGHTS = {"required": 1.0, "other": 0.75, "preferred": 0.35}


def _is_heading(line):
    return len(line) <= 70 and (line.endswith(":") or not re.search(r"[.!?]$", line)) and len(line.split()) <= 8


def skill_sections(text, skills):
    """{skill: "required" | "preferred" | "other"} for where each listing skill shows on the page. A skill under a
    "Requirements" heading is required; under "Preferred" / "Nice to have", or in a line saying "a plus", preferred.
    A skill found in both counts as required."""
    from jobfinder.profiles.profile_tools import SKILL_ALIASES

    patterns = {}
    for skill in skills:
        names = {skill}
        for name, aliases in SKILL_ALIASES.items():
            if skill.casefold() in {item.casefold() for item in [name, *aliases]}:
                names.update([name, *aliases])
        patterns[skill] = re.compile(r"(?<![\w])(?:" + "|".join(re.escape(name) for name in names) + r")(?![\w])", re.I)
    rank = {"other": 0, "preferred": 1, "required": 2}
    found = {}
    section = "other"
    for line in (part.strip() for part in re.split(r"\n+", text or "")):
        if not line:
            continue
        if _is_heading(line):
            section = ("preferred" if PREFERRED_HEADING.search(line) else
                       "required" if REQUIRED_HEADING.search(line) else section)
            if not any(pattern.search(line) for pattern in patterns.values()):
                continue
        for sentence in _sentences(line):
            where = "preferred" if PREFERRED.search(sentence) else section
            for skill, pattern in patterns.items():
                if pattern.search(sentence) and rank[where] >= rank.get(found.get(skill), -1):
                    found[skill] = where
    return {skill: found.get(skill, "other") for skill in skills}


def weighted_fit(user_skills, sections):
    """{"score": 0-100, "required": n, "have": n, "listed": n, "have_listed": n, "missing_required": [...]}.

    With skills under a Requirements heading, missing one of those costs more than missing a nice-to-have. A listing
    whose skills are all nice-to-haves or just mentioned has no skill requirement: having them only helps (from 50
    up to 100), and missing them doesn't count against you. None when the listing has no skills."""
    if not sections or not user_skills:
        return None
    user = {skill.casefold() for skill in user_skills}
    required = [skill for skill, where in sections.items() if where == "required"]
    listed_have = [skill for skill in sections if skill.casefold() in user]
    if required:
        total = sum(WEIGHTS[where] for where in sections.values())
        have = sum(WEIGHTS[where] for skill, where in sections.items() if skill.casefold() in user)
        score = round(100 * have / total)
    else:
        score = round(50 + 50 * len(listed_have) / len(sections))
    return {"score": score, "required": len(required),
            "have": sum(1 for skill in required if skill.casefold() in user),
            "listed": len(sections), "have_listed": len(listed_have),
            "missing_required": [skill for skill in required if skill.casefold() not in user]}


DUTIES_HEADING = re.compile(r"\b(responsibilities|what you(?:'ll| will) do|the role|duties|day[\s-]to[\s-]day|"
                            r"you will|in this role|key tasks|your impact|purpose of (?:the )?role)\b", re.I)


def key_lines(text, per_section=8):
    """{"Requirements": [...], "Responsibilities": [...], "Nice to Have": [...]}: the lines under those headings, for
    skimming a posting. Sections with nothing found are left out."""
    sections = {"Requirements": [], "Responsibilities": [], "Nice to Have": []}
    current = None
    for raw in re.split(r"\n+", text or ""):
        bullet = bool(re.match(r"\s*[•·\-–*+�]", raw))
        line = re.sub(r"\s+", " ", raw.replace("�", " ")).strip(" •·-–*+\t")
        if not line:
            continue
        if not bullet and _is_heading(line):
            kind = ("Nice to Have" if PREFERRED_HEADING.search(line) else
                    "Responsibilities" if DUTIES_HEADING.search(line) else
                    "Requirements" if REQUIRED_HEADING.search(line) else None)
            title_case = len(line.split()) <= 5 and all(word[:1].isupper() or word.lower() in {"and", "of", "&", "the", "to", "for"}
                                                         for word in line.split())
            if kind or line.endswith(":") or title_case or not current:
                current = kind
                continue
        if current and 12 <= len(line) <= 400 and len(sections[current]) < per_section and line not in sections[current]:
            sections[current].append(line)
    return {name: lines for name, lines in sections.items() if lines}


def posting_body(text):
    """The posting text without the menus and links before it: starts a few lines above the first real paragraph,
    with blank-ish lines and stray symbols cleaned up."""
    lines = [re.sub(r"\s+", " ", line.replace("�", "•")).strip() for line in (text or "").split("\n")]
    lines = [line for line in lines if line]
    first = next((n for n, line in enumerate(lines) if len(line.split()) >= 10), 0)
    return "\n".join(lines[max(0, first - 3):])
