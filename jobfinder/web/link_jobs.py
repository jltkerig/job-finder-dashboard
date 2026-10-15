"""Add a job to the Apply Queue from a link the user pastes (a posting on any site).

The posting is read like any other page, saved as a job on the Dashboard (marked as added by you) and queued. Nothing is
submitted anywhere. A link that is already saved is only queued, never overwritten.
"""
import json
import re
from datetime import datetime
from urllib.parse import urlparse

from bs4 import BeautifulSoup
from flask import jsonify, request
from mysql.connector import Error

from jobfinder import db
from jobfinder.profiles.requirements import page_text
from jobfinder.search.relevance import fetch_text
from jobfinder.search.storage import save_company
from jobfinder.sources.posting_text import posting_text
from jobfinder.web.core import api_error, app
from jobfinder.web.schema import ensure_job_tracking_columns, ensure_keep_column

MAX_TEXT = 20000


def clean_url(url):
    url = str(url or "").strip()[:2000]
    if url and not re.match(r"^https?://", url, re.I):
        url = "https://" + url
    parts = urlparse(url)
    return url if parts.scheme in ("http", "https") and "." in (parts.hostname or "") and " " not in url else ""


# "Houston, Texas, United States" or "Baltimore, MD" in a page title is where the job is, not who the employer is.
_PLACE = re.compile(r"(?:,\s*(?:United States|USA|US)\s*$)|(?:^[A-Z][A-Za-z .'-]+,\s*[A-Z]{2}\s*(?:\d{5})?$)|(?:^[A-Z][A-Za-z .'-]+,\s*[A-Z][a-z]+(?:\s[A-Z][a-z]+)*$)")


def title_and_company(html, host):
    """(job title, company) guessed from a posting page's headings; LinkedIn's "Acme hiring Web Designer in Town | LinkedIn" too."""
    soup = BeautifulSoup(html or "", "html.parser")

    def meta(*names):
        for name in names:
            tag = soup.find("meta", attrs={"property": name}) or soup.find("meta", attrs={"name": name})
            if tag and tag.get("content"):
                return tag["content"].strip()
        return ""
    title = meta("og:title", "twitter:title") or (soup.title.string.strip() if soup.title and soup.title.string else "")
    hiring = re.match(r"^(.+?) hiring (.+?)(?: in .+?)?\s*\|\s*LinkedIn$", title)
    if hiring:
        return hiring.group(2).strip()[:150], hiring.group(1).strip()[:150]
    company = meta("og:site_name", "application-name")
    pieces = [p.strip() for p in re.split(r"\s+[|\-–—·]\s+| at ", title) if p.strip()]
    if len(pieces) > 1:
        title, rest = pieces[0], pieces[1:]
        company = company or next((p for p in rest if not re.search(r"\b(jobs?|careers?|apply)\b", p, re.I) and not _PLACE.search(p)), "")
    company = "" if _PLACE.search(company or "") else company
    if not company and host.count("."):
        company = host.split(".")[-2].capitalize()
    return title[:150], company[:150]


def read_posting(url):
    host = (urlparse(url).hostname or "").casefold()
    page = fetch_text(url)
    html = (page.text if page else "") or ""
    text = BeautifulSoup(posting_text(url) or "", "html.parser").get_text("\n", strip=True)
    if len(text) < 600 and html:
        text = page_text(html)
    title, company = title_and_company(html, host)
    return title, company, re.sub(r"\n{3,}", "\n\n", text).strip()[:MAX_TEXT], host


def add_by_link(url):
    """Save the job at `url` (if it isn't saved yet) and put it in the Apply Queue. Returns {id, title, company, already}."""
    ensure_keep_column()
    ensure_job_tracking_columns()
    title, company, text, host = read_posting(url)
    connection = db.connect()
    try:
        cursor = connection.cursor(dictionary=True)
        cursor.execute("SELECT id, name, career_job_title FROM companies WHERE source_url = %s LIMIT 1", (url,))
        found = cursor.fetchone()
        already = bool(found)
        if not found:
            details = {"added_by_link": True, "description": text, "evidence": ["You added this link"],
                       "captured_by": "link", "site": host}
            save_company(connection, company or host, title or "Job", None, host, url, url, "", None, None,
                         source_type="Added by link", listing_details=details)
            cursor.execute("SELECT id, name, career_job_title FROM companies WHERE source_url = %s LIMIT 1", (url,))
            found = cursor.fetchone()
        cursor.execute("SELECT listing_details FROM companies WHERE id = %s", (found["id"],))
        details = json.loads((cursor.fetchone() or {}).get("listing_details") or "{}")
        if not details.get("apply_queued"):
            details["apply_queued_at"] = datetime.now().isoformat(timespec="seconds")
        details["apply_queued"] = True
        cursor.execute("UPDATE companies SET listing_details = %s, is_kept = 1 WHERE id = %s", (json.dumps(details), found["id"]))
        cursor.execute("UPDATE companies SET application_status = 'Saved' WHERE id = %s AND COALESCE(application_status, 'None') IN ('None', '')",
                       (found["id"],))
        connection.commit()
        return {"id": found["id"], "title": found["career_job_title"], "company": found["name"], "already": already}
    finally:
        connection.close()


@app.route("/apply-queue/add-link", methods=["POST"])
def add_link_to_apply_queue():
    url = clean_url((request.get_json(silent=True) or {}).get("url"))
    if not url:
        return jsonify({"status": "error", "message": "That doesn't look like a web address."}), 400
    try:
        return jsonify({"status": "added", **add_by_link(url)})
    except Error as error:
        return api_error("E3243", f"Could not add that job: {error}", 500)
