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


def occupation_skill_suggestions(title, limit=25):
    """Offer examples associated with the occupation, never inferred job requirements."""
    _, _, _, software = _catalog()
    ranked = {}
    for code in _occupation_codes(title):
        for name, priority in software[code].items():
            ranked[name] = max(priority, ranked.get(name, 0))
    return [name for name, _ in sorted(ranked.items(), key=lambda item: (-item[1], item[0].casefold()))[:limit]]


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
    return [name.title() for name in ranked[:limit]]
