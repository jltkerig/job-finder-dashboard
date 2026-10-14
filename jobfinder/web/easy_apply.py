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


def add_to(companies):
    """Put "easy_apply" (True or False) on each saved job from its listing_details."""
    for company in companies:
        company["easy_apply"] = is_easy_apply(company.get("listing_details"))
    return companies
