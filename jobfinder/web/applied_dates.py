"""The date you applied to a job: saved as "applied_on" (YYYY-MM-DD) in the job's listing_details.

Mark Applied and choosing the Applied status save today's date; the date can be changed later in the job's editor on
the Dashboard. No database column is needed, so nothing about the saved jobs table changes.
"""

import json
from datetime import date, datetime

from jobfinder.web import webfiles


def today():
    return datetime.now(webfiles.LOCAL_TIMEZONE).date().isoformat()


def clean(value):
    """The date as YYYY-MM-DD, or "" when it is empty or not a real date."""
    try:
        return date.fromisoformat(str(value or "").strip()).isoformat()
    except ValueError:
        return ""


def _details(text):
    try:
        data = json.loads(text or "{}")
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save(cursor, company_id, status, entered=None, previous=None):
    """Store the date for one job (call before commit).

    entered is the date typed in the editor: None when the form had no date field, "" when it was cleared.
    Becoming Applied (previous is the status before) uses today's date when none is saved or typed; a job that was
    already Applied before dates existed stays without one until you enter it.
    """
    cursor.execute("SELECT listing_details FROM companies WHERE id = %s", (company_id,))
    row = cursor.fetchone()
    if row is None:
        return
    text = row[0] if isinstance(row, (tuple, list)) else row.get("listing_details")
    details = _details(text)
    before = details.get("applied_on", "")
    if entered is None:
        chosen = before
    else:
        chosen = clean(entered)
    if status == "Applied" and not chosen and previous != "Applied":
        chosen = today()
    if chosen:
        details["applied_on"] = chosen
    else:
        details.pop("applied_on", None)
    if details.get("applied_on", "") != before:
        cursor.execute("UPDATE companies SET listing_details = %s WHERE id = %s", (json.dumps(details), company_id))


def add_to(companies):
    """Put "applied_on" (and "applied_label", like Oct 12, 2026) on each saved job from its listing_details."""
    for company in companies:
        company["applied_on"] = clean(_details(company.get("listing_details")).get("applied_on"))
        company["applied_label"] = (date.fromisoformat(company["applied_on"]).strftime("%b %d, %Y")
                                    if company["applied_on"] else "")
    return companies
