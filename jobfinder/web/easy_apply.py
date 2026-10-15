"""LinkedIn's Easy Apply: whether a job you saw on LinkedIn is applied to there, without leaving LinkedIn.

The Web Job Scraper reads it from LinkedIn's own button and the import keeps it in the job's listing_details as
"easy_apply": true. No database column is needed, so nothing about the saved jobs table changes.
"""

import json


def is_easy_apply(listing_details):
    """True when a job's listing_details (JSON text or an already read dict) says it is an Easy Apply job."""
    if isinstance(listing_details, str):
        try:
            listing_details = json.loads(listing_details or "{}")
        except ValueError:
            return False
    return bool(isinstance(listing_details, dict) and listing_details.get("easy_apply"))


def apply_link_is_linkedin(career_url, source_url=""):
    """False when the job's own apply page is somewhere else (say the employer's Workday site): the Easy Apply button was
    on the LinkedIn lead, and Apply no longer goes there, so the badge would promise something the link doesn't do."""
    link = str(career_url or source_url or "")
    return not link or "linkedin.com" in link.lower()


def add_to(companies):
    """Put "easy_apply" (True or False) on each saved job from its listing_details."""
    for company in companies:
        company["easy_apply"] = (is_easy_apply(company.get("listing_details"))
                                 and apply_link_is_linkedin(company.get("career_url"), company.get("source_url")))
    return companies
