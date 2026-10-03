"""Find a job on the company's own hiring board (Ashby, Greenhouse, Lever, ...) from its name and title.

A listing found on a job board is only a copy. Most small companies post the real opening on an applicant-system
board whose address is built from the company name (jobs.ashbyhq.com/aegis-ai). find_ats_posting() tries those
boards, reads each through its public API, and returns the matching opening when one exists.
"""
import re

from jobfinder.sources.employer_jobs import Employer
from jobfinder.sources.employer_site import _GENERIC_TAIL, name_tokens
from jobfinder.sources.job_listings import canonical_url

# Systems whose board address is just the company's name, tried in this order.
SYSTEMS = (("ashby", "slug"), ("greenhouse", "slug"), ("lever", "slug"), ("workable", "slug"),
           ("smartrecruiters", "company"), ("bamboohr", "slug"), ("recruitee", "slug"), ("teamtailor", "slug"))
MAX_SLUGS = 3
_cache = {}


def clear_cache():
    _cache.clear()


def slug_candidates(company):
    """Board names a company might use: 'Aegis AI' -> aegis-ai, aegisai; 'Acme Labs' -> acme-labs, acmelabs, acme."""
    tokens = name_tokens(company)
    variants = []
    while tokens:
        for slug in ("-".join(tokens), "".join(tokens)):
            if len(slug) >= 3 and slug not in variants:
                variants.append(slug)
        if len(tokens) > 1 and tokens[-1] in _GENERIC_TAIL:
            tokens = tokens[:-1]
        else:
            break
    return variants[:MAX_SLUGS]


def _normal(title):
    return re.sub(r"[^a-z0-9]+", " ", str(title or "").casefold()).strip()


def _best(openings, job_title):
    """The opening whose title is the listing's title (or starts with it), or None when it is not clear."""
    wanted = _normal(job_title)
    exact = [opening for opening in openings if _normal(opening["title"]) == wanted]
    if exact:
        return exact[0]
    starts = [opening for opening in openings if _normal(opening["title"]).startswith(wanted + " ")]
    return starts[0] if len(starts) == 1 else None


def find_ats_posting(company, job_title, http):
    """{"system", "url", "title", "board"} for the company's own posting of this job, or None."""
    company = " ".join(str(company or "").split())
    key = (company.casefold(), _normal(job_title))
    if not company or not job_title:
        return None
    if key in _cache:
        return _cache[key]
    found = None
    for slug in slug_candidates(company):
        for system, field in SYSTEMS:
            try:
                employer = Employer({"system": system, field: slug, "name": company, "domain": f"{slug}.example"})
                match = _best(employer.find_openings([job_title], http), job_title)
            except Exception:
                continue
            if match and match.get("url"):
                found = {"system": system, "url": canonical_url(match["url"]) or match["url"], "title": match["title"],
                         "board": slug}
                break
        if found:
            break
    _cache[key] = found
    return found
