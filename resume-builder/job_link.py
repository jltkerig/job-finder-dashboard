"""Read a job posting from a link the user pastes, so a request for a tailored résumé and cover letter can carry it.

Uses Job Finder's own polite page reader (and its Workday / Oracle readers) when available. Nothing is saved: the posting
text only goes into the request the user copies for Claude Desktop.
"""
import re
import sys
from urllib.parse import urlparse

import config

MAX_TEXT = 12000


def _job_finder():
    if str(config.JOB_FINDER_DIR) not in sys.path:
        sys.path.append(str(config.JOB_FINDER_DIR))


def clean_url(url):
    """The link with https:// added when left off; "" when it isn't a web address."""
    url = str(url or "").strip()[:2000]
    if url and not re.match(r"^https?://", url, re.I):
        url = "https://" + url
    parts = urlparse(url)
    return url if parts.scheme in ("http", "https") and "." in (parts.hostname or "") and " " not in url else ""


def _title_and_company(html, host):
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html or "", "html.parser")

    def meta(*names):
        for name in names:
            tag = soup.find("meta", attrs={"property": name}) or soup.find("meta", attrs={"name": name})
            if tag and tag.get("content"):
                return tag["content"].strip()
        return ""
    title = meta("og:title", "twitter:title") or (soup.title.string.strip() if soup.title and soup.title.string else "")
    company = meta("og:site_name", "application-name")
    pieces = [p.strip() for p in re.split(r"\s+[|\-–—·]\s+| at ", title) if p.strip()]
    if len(pieces) > 1:
        title, rest = pieces[0], pieces[1:]
        company = company or next((p for p in rest if not re.search(r"\b(jobs?|careers?|apply)\b", p, re.I)), "")
    if not company:
        company = host.split(".")[-2].capitalize() if host.count(".") else ""
    return title[:150], company[:150]


def read(url):
    """{"url", "title", "company", "text", "note"}: what could be read from the link ("text" is "" when nothing could)."""
    url = clean_url(url)
    if not url:
        return {"url": "", "title": "", "company": "", "text": "", "note": "That doesn't look like a web address."}
    host = (urlparse(url).hostname or "").casefold()
    html, note = "", ""
    try:
        _job_finder()
        from jobfinder.search.relevance import fetch_text
        from jobfinder.sources.posting_text import posting_text
        page = fetch_text(url)
        html = (page.text if page else "") or ""
        system_text = posting_text(url)  # Workday, Oracle and similar pages build themselves with JavaScript
    except Exception:  # Job Finder's reader unavailable: the request still carries the link
        system_text = ""
    from bs4 import BeautifulSoup
    title, company = _title_and_company(html, host)
    text = ""
    if system_text:
        text = BeautifulSoup(system_text, "html.parser").get_text("\n", strip=True)
    if len(text) < 600 and html:
        try:
            from jobfinder.profiles.requirements import page_text
            text = page_text(html)
        except Exception:
            text = BeautifulSoup(html, "html.parser").get_text("\n", strip=True)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if len(text) < 400:
        note = ("The page didn't give much text (many job sites build the page with JavaScript). "
                "The request will carry the link, and Claude Desktop will open it.")
    return {"url": url, "title": title, "company": company, "text": text[:MAX_TEXT], "note": note}


def request_text(url, title, company, text):
    """The request to paste into Claude Desktop."""
    label = " at ".join(part for part in (title, company) if part)
    lines = ["Using Résumé Builder, read my writing rules, then tailor my résumé and write a cover letter for this job"
             + (f" ({label})" if label else "") + f". Job link: {url}",
             "Read the whole listing first, then save both as PDFs (pass the job title and company when you save them)."]
    if text:
        lines += ["", "Posting text, in case the link needs a login or won't open:", "---", text, "---"]
    return "\n".join(lines)
