"""Occupation and software skill suggestions from the bundled O*NET 31.0 files.

This module reads downloaded database files locally; it never calls Web Services.
"""
import csv
import re
from collections import defaultdict
from functools import lru_cache
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent / "data" / "onet-31.0"


def _rows(filename):
    with (DATA_DIR / filename).open(encoding="utf-8-sig", newline="") as stream:
        yield from csv.DictReader(stream, delimiter="\t")


def _key(value):
    return re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()


# Job titles are always suggested with proper capitalization ("UX/UI Designer", "Front-End Developer"); matching a
# search ignores case, so how a title is capitalized never changes what is found.
_SMALL_WORDS = {"a", "an", "and", "as", "at", "but", "by", "for", "in", "of", "on", "or", "the", "to", "with"}
_KEEP_UPPER = {"ux", "ui", "qa", "it", "seo", "sem", "cms", "html", "css", "php", "sql", "api", "hr", "crm", "erp", "pr", "ai",
               "ml", "vp", "ceo", "cfo", "cto", "coo", "aws", "ii", "iii", "iv", "3d", "2d", "ar", "vr", "b2b", "b2c", "ehr",
               "emr", "cad", "gis", "rn", "lpn", "cdl", "hvac", "usa", "us", "uk", "ios", "net", "sap", "etl", "bi", "pc", "av", "tv"}
_SPECIAL = {"wordpress": "WordPress", "javascript": "JavaScript", "typescript": "TypeScript", "linkedin": "LinkedIn",
            "devops": "DevOps", "powerpoint": "PowerPoint", "photoshop": "Photoshop", "ios": "iOS", "macos": "macOS",
            "youtube": "YouTube", "salesforce": "Salesforce", "github": "GitHub", "nodejs": "Node.js"}


def _proper_word(word):
    lower = word.casefold()
    if lower in _SPECIAL:
        return _SPECIAL[lower]
    if lower in _KEEP_UPPER:
        return lower.upper() if lower != "ios" else "iOS"
    if any(c.isupper() for c in word[1:]) and any(c.islower() for c in word):
        return word  # already written in its own style (WordPress, eBay)
    return lower[:1].upper() + lower[1:]


def proper_title(text):
    """A job title in proper capitalization: every word capitalized except small joining words, acronyms kept in capitals."""
    words = re.sub(r"\s+", " ", str(text or "")).strip().split(" ")
    out = []
    for n, word in enumerate(words):
        if not word:
            continue
        parts = re.split(r"([/\-])", word)
        done = "".join(part if part in "/-" else (part.casefold() if part.casefold() in _SMALL_WORDS and 0 < n < len(words) - 1 and len(parts) == 1
                                                 else _proper_word(part)) for part in parts)
        out.append(done)
    return " ".join(out)


@lru_cache(maxsize=1)
def _catalog():
    occupations = {row["O*NET-SOC Code"]: row["Title"] for row in _rows("Occupation Data.txt")}
    titles = defaultdict(set)
    for row in _rows("Job Titles.txt"):
        titles[_key(row["Job Title"])].add(row["O*NET-SOC Code"])
    for code, title in occupations.items():
        titles[_key(title)].add(code)
    by_occupation = defaultdict(set)
    for name, codes in titles.items():
        for code in codes:
            by_occupation[code].add(name)
    software = defaultdict(dict)
    for row in _rows("Software Skills.txt"):
        name = row["Workplace Example"].strip()
        if name and len(name) <= 80:
            code = row["O*NET-SOC Code"]
            priority = (row["In Demand"] == "Y") * 2 + (row["Hot Technology"] == "Y")
            software[code][name] = max(priority, software[code].get(name, 0))
    return occupations, titles, by_occupation, software


def _occupation_codes(title):
    occupations, titles, _, _ = _catalog()
    key = _key(title)
    if not key:
        return []
    exact = titles.get(key)
    if exact:
        return sorted(exact, key=lambda code: (occupations.get(code, "").casefold() != key, code))[:3]
    # Only a close title match should introduce occupational skills.
    candidates = [(name, codes) for name, codes in titles.items() if name.startswith(key + " ")]
    if len(candidates) == 1:
        return sorted(candidates[0][1])[:2]
    return []


@lru_cache(maxsize=1)
def _known_words():
    _, titles, _, _ = _catalog()
    return {word for name in titles for word in name.split() if len(word) >= 3}


def spelling_fix(title):
    """The title with misspelled words corrected against O*NET's job-title words, or the title itself when it reads fine.

    "production specalist" -> "production specialist". A word is changed only when it is not a known word and one
    known word is a very close match.
    """
    import difflib
    known = _known_words()
    words = str(title or "").split()
    fixed = []
    for word in words:
        bare = re.sub(r"[^a-z]", "", word.casefold())
        if len(bare) >= 5 and bare not in known:
            close = difflib.get_close_matches(bare, known, n=1, cutoff=0.86)
            if close:
                word = close[0].upper() if word.isupper() else close[0].capitalize() if word[:1].isupper() else close[0]
        fixed.append(word)
    return " ".join(fixed)


def occupation_skill_suggestions(title, limit=25):
    """Offer examples associated with the occupation, never inferred job requirements."""
    _, _, _, software = _catalog()
    ranked = {}
    for code in _occupation_codes(title):
        for name, priority in software[code].items():
            ranked[name] = max(priority, ranked.get(name, 0))
    return [name for name, _ in sorted(ranked.items(), key=lambda item: (-item[1], item[0].casefold()))[:limit]]


@lru_cache(maxsize=1)
def _title_names():
    """Every O*NET job title once, as written ("Web Designer"), with its search key."""
    names = {}
    for row in _rows("Occupation Data.txt"):
        names.setdefault(_key(row["Title"]), row["Title"].strip())
    for row in _rows("Job Titles.txt"):
        names.setdefault(_key(row["Job Title"]), row["Job Title"].strip())
    # Occupation names are plural ("Graphic Designers"); keep only the singular when both exist.
    names = {k: v for k, v in names.items() if not (k.endswith("s") and k[:-1] in names)}
    return sorted(names.items(), key=lambda item: (len(item[0]), item[0]))


def title_matches(text, limit=10):
    """Job titles for type-ahead: each typed word must start a word of the title.

    Titles that begin with what was typed come first, then shorter titles.
    """
    key = _key(text)
    if len(key) < 2:
        return []
    words = key.split()
    starts, others = [], []
    for name, title in _title_names():
        title_words = name.split()
        if all(any(w.startswith(typed) for w in title_words) for typed in words):
            (starts if name.startswith(key) else others).append(title)
            if len(starts) >= limit:
                break
    return [proper_title(title) for title in (starts + others)[:limit]]


def related_title_suggestions(title, limit=8):
    _, _, by_occupation, _ = _catalog()
    key = _key(title)
    candidates = set()
    for code in _occupation_codes(title):
        candidates.update(by_occupation[code])
    # Avoid very broad or oddly specific variations in the first suggestions.
    candidates.discard(key)
    tokens = set(key.split())
    ranked = sorted(candidates, key=lambda name: (-len(tokens & set(name.split())), len(name), name))
    return [proper_title(name) for name in ranked[:limit]]
