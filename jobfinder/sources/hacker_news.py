"""Hacker News "Ask HN: Who is hiring?" as a remote-job feed.

Every month the account `whoishiring` posts a thread in which companies add one top-level comment per opening, usually
starting with a line like "Acme | Frontend Engineer | REMOTE (US) | Full-time". The thread and its comments come from the
free Hacker News search service (hn.algolia.com), which needs no key. Each match links to its comment on Hacker News, which
is where the company's own apply link or email is. Only remote openings open to U.S. applicants are kept, since the feed is
saved as remote work like the other feeds.
"""
import re
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

from jobfinder.sources.job_listings import matching_title

SEARCH_URL = "https://hn.algolia.com/api/v1"
USER_AGENT = "PersonalJobFinder/1.1 (local job search)"
CACHE_SECONDS = 24 * 3600
MAX_PAGES = 4
THREAD_TITLE = re.compile(r"who is hiring", re.I)
NOT_THIS_THREAD = re.compile(r"wants to be hired|freelancer", re.I)
REMOTE = re.compile(r"\bremote\b", re.I)
US_WORDS = re.compile(r"\b(?:USA?|United States|U\.S\.)\b|\bNorth America\b|\bAmericas\b", re.I)
OTHER_REGION = re.compile(r"\b(?:EU|Europe|European|EMEA|UK|United Kingdom|Canada|India|APAC|LATAM|Germany|France|Spain|Poland|"
                          r"Brazil|Australia|Israel|Ireland|Netherlands|Portugal|Mexico)\b", re.I)
ANYWHERE = re.compile(r"\b(?:anywhere|worldwide|global)\b", re.I)
ROLE_SPLIT = re.compile(r"\s*/\s*|\s*,\s*|\s+and\s+|\s+or\s+|\s*;\s*")


def _comments(timeout):
    """[{"id", "text", "created_at"}] for the newest "Who is hiring?" thread's top-level comments."""
    headers = {"User-Agent": USER_AGENT}
    found = requests.get(f"{SEARCH_URL}/search_by_date", params={"tags": "story,author_whoishiring", "hitsPerPage": 12},
                         headers=headers, timeout=timeout)
    found.raise_for_status()
    thread = next((hit for hit in found.json().get("hits", [])
                   if THREAD_TITLE.search(hit.get("title") or "") and not NOT_THIS_THREAD.search(hit.get("title") or "")), None)
    if not thread:
        raise ValueError("No Who is hiring thread found")
    story = str(thread["objectID"])
    comments, page = [], 0
    while page < MAX_PAGES:
        response = requests.get(f"{SEARCH_URL}/search", params={"tags": f"comment,story_{story}", "hitsPerPage": 1000, "page": page},
                                headers=headers, timeout=timeout)
        response.raise_for_status()
        data = response.json()
        for hit in data.get("hits", []):
            if str(hit.get("parent_id")) == story and hit.get("comment_text"):  # an opening, not a reply to one
                comments.append({"id": str(hit["objectID"]), "text": hit["comment_text"], "created_at": hit.get("created_at")})
        page += 1
        if page >= int(data.get("nbPages") or 1):
            break
    return comments


def fetch_jobs(timeout=20):
    """The thread's comments, kept on disk for a day (the thread changes once a month)."""
    from jobfinder.sources.job_feeds import _cached  # here: job_feeds imports this module
    return _cached("hackernews-who-is-hiring", CACHE_SECONDS, lambda: _comments(timeout))


def _lines(html):
    soup = BeautifulSoup(html or "", "html.parser")
    return [line.strip() for line in soup.get_text("\n").split("\n") if line.strip()], soup


def parse_comment(comment):
    """{"company", "header", "parts", "text", "html", "links"} from a comment's first line, or None when it has no header."""
    lines, soup = _lines(comment.get("text"))
    if not lines:
        return None
    header = lines[0]
    parts = [part.strip() for part in re.split(r"\s*\|\s*", header) if part.strip()]
    if len(parts) < 2:
        return None  # "Company | Role | Place | ..." is the habit; free-form comments can't be read reliably
    company = re.sub(r"\s*\((?:[^)]*)\)\s*|\s*https?://\S+", " ", parts[0]).strip(" -:–—")
    if not company or len(company) > 80:
        return None
    links = [a["href"] for a in soup.find_all("a", href=True) if a["href"].startswith("https://")]
    return {"company": company, "header": header, "parts": parts[1:], "lines": lines, "html": comment.get("text") or "",
            "text": " ".join(lines), "links": links}


def _role_candidates(parsed):
    """Pieces of the header and any "Hiring:" line that could be a job title."""
    pieces = []
    texts = list(parsed["parts"]) + [line for line in parsed["lines"][1:6] if re.match(r"(?:hiring|roles?|positions?|open roles?)\b", line, re.I)]
    for text in texts:
        for piece in ROLE_SPLIT.split(re.sub(r"^(?:hiring|roles?|positions?|open roles?)\s*:\s*", "", text, flags=re.I)):
            piece = piece.strip(" -:–—")
            if 3 <= len(piece) <= 90:
                pieces.append(piece)
    return pieces


def _location(parsed):
    """The header pieces that say where, and the U.S. score: 6 when it names the U.S., 5 when it is open to anywhere."""
    header = parsed["header"]
    place = " | ".join(part for part in parsed["parts"] if REMOTE.search(part) or US_WORDS.search(part) or ANYWHERE.search(part)
                       or OTHER_REGION.search(part))
    if not REMOTE.search(header):
        return None, 0
    if US_WORDS.search(header):
        return place or "Remote (US)", 6
    if ANYWHERE.search(header):
        return place or "Remote (anywhere)", 5
    if OTHER_REGION.search(header):
        return place, 0  # "REMOTE (EU)": not open to U.S. applicants
    return place or "Remote", 5  # "REMOTE" with no region named


def matching_jobs(comments, titles, usa_only=True):
    """Openings whose header names a title you search for, remote and open to U.S. applicants."""
    for comment in comments:
        parsed = parse_comment(comment)
        if not parsed:
            continue
        location, score = _location(parsed)
        if location is None or (usa_only and not score):
            continue
        title = next((piece for piece in _role_candidates(parsed) if matching_title(piece, titles)), None)
        if not title:
            continue
        url = f"https://news.ycombinator.com/item?id={comment['id']}"
        if urlparse(url).hostname != "news.ycombinator.com":
            continue
        yield {"name": parsed["company"][:255], "title": title[:255], "url": url, "location": location[:100],
               "html": parsed["html"][:200000], "text": parsed["text"][:200000], "posted": comment.get("created_at"),
               "usa_score": score}
