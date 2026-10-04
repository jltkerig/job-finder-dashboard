"""Fill the Job Finder profile from an uploaded resume.

Only fills gaps: a name, city or primary title already set is kept, skills are only added,
and a job is only added when its title and company aren't in the work history yet.
"""
import re
import sys

import config
import jobfinder_db

INVISIBLE = re.compile(r"[​‌‍⁠﻿]")
BULLET = re.compile(r"^[●•▪◦‣∙·\-\*–]\s*")
MONTH = r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\.?"
YEAR = r"(?:19|20)\d{2}"
WHEN = rf"(?:{MONTH}\s+{YEAR}|\d{{1,2}}/{YEAR}|{YEAR})"
DATES = re.compile(rf"^{WHEN}\s*(?:[-–—]|to)\s*(?:{WHEN}|present|current|now)$", re.I)
PLACE = re.compile(r"^(?:remote|hybrid|on[- ]?site|in[- ]office|[A-Za-z .'-]{2,40},\s*(?:[A-Z]{2}|[A-Za-z ]{4,20}))$", re.I)
HEADINGS = re.compile(r"^(?:professional\s+)?(?:summary|profile|objective|(?:employment|work|professional)?\s*(?:history|experience)|"
                      r"education(?:\s+history)?|skills|technical skills|certifications?|projects|awards|additional experience|"
                      r"volunteer(?:ing| experience)?|references|interests|languages)$", re.I)
NAME = re.compile(r"^[A-Z][A-Za-z'.-]+(?:\s+[A-Z][A-Za-z'.-]+){1,3}$")


def _clean_lines(text):
    lines = [INVISIBLE.sub("", line).strip() for line in text.splitlines()]
    return [line for line in lines if line]


def _header(lines):
    """Lines above the first section heading: name and contact details."""
    for i, line in enumerate(lines):
        if HEADINGS.match(line):
            return lines[:i]
    return lines[:8]


def extract(text):
    lines = _clean_lines(text)
    header = _header(lines)
    name = next((l for l in header[:3] if NAME.match(l) and not HEADINGS.match(l)), "")
    location = next((l for l in header if PLACE.match(l) and "," in l), "")
    # A job is: title, company, optional place line, then a dates line, then its bullets.
    entries = []  # (first line of the entry, dates line, role, company)
    for i, line in enumerate(lines):
        if not DATES.match(line):
            continue
        top = i - 1
        if top >= 0 and PLACE.match(lines[top]):
            top -= 1
        if top < 1 or HEADINGS.match(lines[top]) or HEADINGS.match(lines[top - 1]):
            continue
        entries.append((top - 1, i, lines[top - 1], lines[top]))
    jobs = []
    for n, (start, dates_row, role, company) in enumerate(entries):
        end = entries[n + 1][0] if n + 1 < len(entries) else len(lines)
        bullets, current = [], ""
        for line in lines[dates_row + 1:end]:
            if HEADINGS.match(line):
                break
            if BULLET.match(line):
                if current:
                    bullets.append(current)
                current = BULLET.sub("", line)
            else:
                current = f"{current} {line}".strip()
        if current:
            bullets.append(current)
        jobs.append({"role": role[:150], "company": company[:150], "dates": lines[dates_row][:100],
                     "description": "\n".join(f"• {b}" for b in bullets if b)[:3000]})
    better = _jobfinder_jobs(text)
    return {"name": name, "location": location, "skills": _detect_skills(" ".join(lines)), "work_history": better or jobs}


def _jobfinder_jobs(text):
    """The jobs in the resume as Job Finder's own reader finds them (the one the Dashboard uses); [] if unavailable."""
    if str(config.JOB_FINDER_DIR) not in sys.path:
        sys.path.append(str(config.JOB_FINDER_DIR))
    try:
        from jobfinder.profiles.profile_tools import parse_work_history
    except ImportError:
        return []
    return parse_work_history(text)


def new_jobs(text, profile):
    """Jobs the resume lists that the profile's work history doesn't have yet (matched on title and company)."""
    known = {(w["role"].casefold(), w["company"].casefold()) for w in profile["work_history"]}
    return [j for j in extract(text)["work_history"] if (j["role"].casefold(), j["company"].casefold()) not in known]


def _detect_skills(text):
    if str(config.JOB_FINDER_DIR) not in sys.path:
        sys.path.append(str(config.JOB_FINDER_DIR))
    try:
        from jobfinder.profiles.profile_tools import detect_skills
    except ImportError:
        return []
    return detect_skills(text)


def fill_profile(text):
    """Merge what the resume says into the Job Finder profile. Returns a list of what was added."""
    found = extract(text)
    profile = jobfinder_db.get_profile()
    added = []
    first, last = profile["first_name"], profile["last_name"]
    if not (first or last) and found["name"]:
        first, _, last = found["name"].partition(" ")
        added.append("name")
    location = profile["home_location"]
    if not location and found["location"]:
        location = found["location"]
        added.append("location")
    have = {s.casefold() for s in profile["skills"]}
    new_skills = [s for s in found["skills"] if s.casefold() not in have]
    if new_skills:
        added.append(f"{len(new_skills)} skill{'s' if len(new_skills) != 1 else ''}")
    known = {(w["role"].casefold(), w["company"].casefold()) for w in profile["work_history"]}
    new_jobs = [j for j in found["work_history"] if (j["role"].casefold(), j["company"].casefold()) not in known]
    if new_jobs:
        added.append(f"{len(new_jobs)} job{'s' if len(new_jobs) != 1 else ''}")
    primary = profile["primary_job_title"]
    others = [t for t in profile["job_titles"] if t != primary]
    # Most recent job title becomes the primary only if it's one of the titles already searched for.
    if not primary and found["work_history"]:
        latest = found["work_history"][0]["role"]
        match = next((t for t in profile["job_titles"] if t.casefold() == latest.casefold()), "")
        if match:
            primary, others = match, [t for t in others if t != match]
            added.append("primary job title")
    if not added:
        return []
    if not (first and last):
        return []  # Job Finder's form requires a full name; nothing saved without one
    jobfinder_db.save_profile(first, last, location, primary, others, profile["skills"] + new_skills,
                              profile["work_history"] + new_jobs)
    return added
