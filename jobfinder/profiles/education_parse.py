"""Schools listed under a résumé's education heading, to pre-fill the Dashboard's Education section.

Like the work history, this is a suggestion: the form shows it for the person to check, and nothing is saved until
Save Profile. Anything it cannot place is left blank rather than guessed (a year alone is not a month, so it stays out).
"""

import re

from jobfinder.profiles.profile_tools import (
    _EXPERIENCE_HEADING, _MONTH_NAMES, _MONTHS, _OTHER_HEADING, _PLACE_LINE, _clean_lines)

_HEADING = re.compile(r"^(?:education(?:al)?(?:\s+(?:history|background|and\s+training|&\s+training|summary))?|"
                      r"academic\s+(?:history|background)|schooling)\s*:?$", re.I)
# Headings that end the section besides the ones profile_tools already knows.
_MORE_HEADINGS = re.compile(r"^(?:honou?rs(?:\s*(?:&|and)\s*awards)?|activities|languages|publications|training|"
                            r"affiliations|memberships|leadership|achievements|accomplishments|community|hobbies|"
                            r"portfolio|contact|links|tools|software|expertise|highlights)\s*:?$", re.I)
_SCHOOL = re.compile(r"\b(?:university|college|institute|school|academy|polytechnic|seminary|conservatory)\b", re.I)
_END = r"(?=[\s,.:;()]|$)"
# The first match wins, so the narrower degrees come before the broader ones. Abbreviations are case-sensitive.
_DEGREES = (
    ("Professional Degree", re.compile(r"\bjuris doctor\b|\bdoctor of (?:medicine|dental|pharmacy|law)\b", re.I)),
    ("Professional Degree", re.compile(r"\b(?:J\.?D|M\.?D|D\.?D\.?S|Pharm\.?D)\.?" + _END)),
    ("Doctorate", re.compile(r"\bdoctor(?:ate|al)?\b|\bph\.?\s?d\b|\bEd\.?D\b", re.I)),
    ("Master's Degree", re.compile(r"\bmaster(?:'s|’s|s)?\b", re.I)),
    ("Master's Degree", re.compile(r"\b(?:M\.?S|M\.?A|M\.?B\.?A|M\.?F\.?A|M\.?Ed|M\.?Eng)\.?" + _END)),
    ("Bachelor's Degree", re.compile(r"\bbachelor(?:'s|’s|s)?\b", re.I)),
    ("Bachelor's Degree", re.compile(r"\b(?:B\.?S|B\.?A|B\.?F\.?A|B\.?Sc|B\.?Eng)\.?" + _END)),
    ("Associate's Degree", re.compile(r"\bassociate(?:'s|’s|s)?\b", re.I)),
    ("Associate's Degree", re.compile(r"\b(?:A\.?A\.?S|A\.?A|A\.?S)\.?" + _END)),
    ("High School Diploma", re.compile(r"\bhigh school\b", re.I)),
    ("GED", re.compile(r"\bG\.?E\.?D\b")),
    ("Certificate", re.compile(r"\bcertificate\b|\bdiploma\b", re.I)),
)
_GPA = re.compile(r"\bGPA\b\s*[:\-]?\s*([0-4](?:\.\d{1,2})?)", re.I)
_MINOR = re.compile(r"^minor(?:\s+in|\s*:)?\s+(.+)$", re.I)
_MAJOR_SPLIT = re.compile(r"\s*(?:,|\s[-–—]\s|:|\s+in\s+)\s*", re.I)
_MONTH_YEAR = re.compile(rf"\b({_MONTH_NAMES})\.?\s+((?:19|20)\d{{2}})\b", re.I)
_YEAR = re.compile(r"\b(?:19|20)\d{2}\b")


def _degree_of(line):
    """The drop-down's name for the degree a line starts with, or None. Only the start of the line counts, so a
    sentence that mentions a degree further along is not read as one."""
    for label, pattern in _DEGREES:
        match = pattern.search(line)
        # A written-out degree may follow a word or two ("Graduate Certificate"); an abbreviation must lead the line,
        # or the state in "Springfield, MD" would read as an M.D.
        if match and match.start() <= (25 if pattern.flags & re.I else 0):
            return label, match
    return None


def _major_of(line):
    """"Bachelor of Science, Graphic Design" and "B.S. in Graphic Design" -> "Graphic Design"."""
    parts = _MAJOR_SPLIT.split(line, maxsplit=1)
    if len(parts) == 2 and parts[1].strip():
        return parts[1].strip(" .,;")[:150]
    return ""


def _months(line):
    """YYYY-MM for each "Month YYYY" in a line, in order; years on their own are not months and are left out."""
    return [f"{year}-{_MONTHS[month.lower()]:02d}" for month, year in _MONTH_YEAR.findall(line)]


def parse_education(text, limit=6):
    """Schools under the résumé's education heading: [{"school", "degree", "major", "minor", "start_date", "end_date",
    "gpa"}], in the order listed. Reads a school line, an optional place line, and a "Degree, Major" line (the
    degree may come first); a second degree at the same school is its own entry."""
    lines = _clean_lines(text)
    start = next((i for i, line in enumerate(lines) if _HEADING.match(line)), None)
    if start is None:
        return []
    end = next((i for i in range(start + 1, len(lines))
                if (_OTHER_HEADING.match(lines[i]) and not _HEADING.match(lines[i])) or _EXPERIENCE_HEADING.match(lines[i])
                or _MORE_HEADINGS.match(lines[i])),
               len(lines))
    entries, current = [], None
    degree_first = False  # the degree line came before its school's name, so a new degree is not at the same school

    def blank(school=""):
        return {"school": school, "degree": "", "major": "", "minor": "", "start_date": "", "end_date": "", "gpa": ""}

    for line in lines[start + 1:end]:
        if not line:
            continue
        gpa = _GPA.search(line)
        if gpa and current is not None:
            current["gpa"] = gpa.group(1)
        minor = _MINOR.match(line)
        if minor and current is not None:
            current["minor"] = minor.group(1).strip(" .,;")[:150]
            continue
        degree = _degree_of(line)
        # "Perryville High School" is a school; "High School Diploma" is a degree.
        if degree and (not _SCHOOL.search(line.split(",")[0]) or re.search(r"\b(?:diploma|ged|certificate)\b", line, re.I)):
            if current is None or current["degree"]:
                school = current["school"] if current and not degree_first else ""
                if current:
                    entries.append(current)
                current = blank(school)
            current["degree"] = degree[0]
            major = _major_of(line)
            if _SCHOOL.search(major):  # "Master of Education, Springfield University": the school came after the degree
                if not current["school"]:
                    current["school"] = major[:200]
                    degree_first = True
                major = ""
            current["major"] = major
            months = _months(line)
            if months:
                current["end_date"] = months[-1]
            continue
        months = _months(line)
        if months and not _SCHOOL.search(line):
            if current is not None:
                if len(months) > 1:
                    current["start_date"] = months[0]
                current["end_date"] = months[-1]
            continue
        if _PLACE_LINE.match(line) or _YEAR.fullmatch(line) or gpa:
            continue
        if _SCHOOL.search(line) or (current is None and len(line) <= 90 and not line.endswith(".")):
            if current is not None and not current["school"]:
                degree_first = degree_first or bool(current["degree"])
                current["school"] = line[:200]
                continue
            if current is not None:
                entries.append(current)
            current = blank(line[:200])
    if current is not None:
        entries.append(current)
    return [entry for entry in entries if entry["school"]][:limit]
