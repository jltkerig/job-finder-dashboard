"""Jobs captured by the Web Job Scraper Firefox extension.

The extension saves one jobs.json per site per day into Downloads\\web-job-scraper\\searches\\. The dashboard asks
before moving those files into web-job-scraper\\searches\\ (next to this project), and job_finder.py --import-captures
then filters and saves them. This module holds the file handling and the matching rules; the database work and the
location checks live in job_finder.py.
"""

import csv
import json
import re
import shutil
from datetime import date, timedelta
from html import escape
from pathlib import Path

from jobfinder.sources.job_listings import canonical_url

# Extension site key -> (source_type saved in companies, domain)
SITES = {
    "linkedin": ("LinkedIn", "linkedin.com"),
    "indeed": ("Indeed", "indeed.com"),
    "glassdoor": ("Glassdoor", "glassdoor.com"),
    # Its robots.txt forbids automated visitors: jobs come only from pages you opened, and Refresh never visits it.
    "mwe": ("Maryland Workforce Exchange", "mwejobs.maryland.gov"),
    # Blocks automated visitors too: jobs come only from search pages you opened (the optional API stays separate).
    "usajobs": ("USAJOBS", "usajobs.gov"),
}
# Rows with these source types came from the extension. The dashboard shows them even though their domains are
# on the blocked list (that list is for web-search results), and Refresh never re-fetches them: the sites answer
# job-finder with sign-in pages, which would read as closed jobs.
CAPTURE_SOURCES = frozenset(source for source, _ in SITES.values())
CAPTURE_MARK = "web-job-scraper"
# Error codes printed by job_finder.py --import-captures (E6xxx; the extension uses E7xxx). Listed in the
# web-job-scraper README.
IMPORT_ERRORS = {
    "database": "E6001",   # the database could not be reached
    "schema": "E6002",     # the database could not be updated
    "no_titles": "E6003",  # the profile has no job titles to match
    "save": "E6004",       # a job could not be saved
    "bad_file": "E6005",   # a capture file could not be read
    "files": "E6006",      # imported files could not be recorded, or old folders removed
    "tidy": "E6007",       # closed jobs could not be tidied
    "profile": "E6008",    # the profile could not be read
}
IMPORTED_FILE = ".imported.json"
KEEP_FOLDER_DAYS = 30
CSV_FIELDS = ["site", "job_id", "title", "company", "location", "work_arrangement", "salary", "posted", "level",
              "applied", "closed", "page_kind", "url", "first_seen", "last_seen"]
_LEVELS = {"seen": 0, "opened": 1}
_COMPANY_SUFFIX = re.compile(r"\b(?:inc|incorporated|llc|l\.l\.c|ltd|limited|corp|corporation|co|company|plc|lp|llp)\b\.?", re.I)
_ARRANGEMENT_NOTE = re.compile(r"\((?:remote|hybrid|on-?site)\)", re.I)
_TITLE_NOISE = {"remote", "hybrid", "onsite", "the", "a", "job"}
# Badges LinkedIn adds to job titles ("Web Designer (Verified job)"); older capture files still have them.
_BADGE = re.compile(r"\s*\(verified job\)\s*$|\s+with verification\s*$", re.I)
_FOLDER_DATE = re.compile(r"^passive-(\d{2})-(\d{2})-(\d{4})$")


def default_download_dir(home=None):
    """Where Firefox saved the extension's files: the browser's download folder may be Downloads or the Desktop,
    so the first one holding a web-job-scraper folder wins (Downloads when neither does yet)."""
    home = Path(home or Path.home())
    candidates = [home / "Downloads" / "web-job-scraper", home / "Desktop" / "web-job-scraper"]
    return next((folder for folder in candidates if folder.is_dir()), candidates[0])


def capture_dirs(settings, base_dir):
    """(download capture folder, searches folder), from settings.json or the defaults."""
    downloads = settings.get("capture_downloads_dir") or default_download_dir()
    searches = settings.get("capture_searches_dir") or str(Path(base_dir).parent / "web-job-scraper" / "searches")
    return Path(downloads).expanduser(), Path(searches).expanduser()


def _read_capture(path):
    """The file's contents when it is an extension capture file, else None."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("source") != CAPTURE_MARK or data.get("site") not in SITES:
        return None
    if not isinstance(data.get("jobs"), list):
        return None
    return data


def pending_files(downloads_dir):
    """Capture files waiting in the Downloads folder: [{path, relative, site, jobs, modified}]."""
    root = Path(downloads_dir) / "searches"
    found = []
    if not root.is_dir():
        return found
    for path in sorted(root.rglob("jobs.json")):
        data = _read_capture(path)
        if data is None:
            continue
        found.append({"path": path, "relative": path.relative_to(root).as_posix(), "site": data["site"],
                      "jobs": len(data["jobs"]), "modified": path.stat().st_mtime})
    return found


def write_csv(json_path):
    """A jobs.csv next to jobs.json, for reading the capture in a spreadsheet."""
    data = _read_capture(json_path)
    if data is None:
        return None
    csv_path = Path(json_path).with_name("jobs.csv")
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for job in data["jobs"]:
            if isinstance(job, dict):
                writer.writerow({field: job.get(field, "") for field in CSV_FIELDS})
    return csv_path


def move_pending(downloads_dir, searches_dir):
    """Move waiting capture files into the searches folder (same sub-folders) and write a CSV beside each.

    Each file holds the whole day for its site, so a newer copy replaces the one already there.
    Returns the moved files' new paths.
    """
    searches_dir = Path(searches_dir)
    moved = []
    for item in pending_files(downloads_dir):
        target = searches_dir / item["relative"]
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(item["path"]), str(target))
        write_csv(target)
        moved.append(target)
        try:
            item["path"].parent.rmdir()  # the emptied <site> folder, then its day folder
            item["path"].parent.parent.rmdir()
        except OSError:
            pass
    return moved


def _load_imported(searches_dir):
    try:
        data = json.loads((Path(searches_dir) / IMPORTED_FILE).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def files_to_import(searches_dir):
    """Capture files in the searches folder that changed since they were last imported."""
    searches_dir = Path(searches_dir)
    if not searches_dir.is_dir():
        return []
    imported = _load_imported(searches_dir)
    result = []
    for path in sorted(searches_dir.rglob("jobs.json")):
        relative = path.relative_to(searches_dir).as_posix()
        if imported.get(relative) != path.stat().st_mtime and _read_capture(path) is not None:
            result.append(path)
    return result


def unreadable_files(searches_dir):
    """jobs.json files in the searches folder that are not readable capture files (damaged or half-written)."""
    searches_dir = Path(searches_dir)
    if not searches_dir.is_dir():
        return []
    return [path for path in sorted(searches_dir.rglob("jobs.json")) if _read_capture(path) is None]


def mark_imported(searches_dir, paths):
    searches_dir = Path(searches_dir)
    imported = _load_imported(searches_dir)
    for path in paths:
        path = Path(path)
        if path.exists():
            imported[path.relative_to(searches_dir).as_posix()] = path.stat().st_mtime
    (searches_dir / IMPORTED_FILE).write_text(json.dumps(imported, indent=1, sort_keys=True), encoding="utf-8")


def remove_old_folders(searches_dir, today=None, keep_days=KEEP_FOLDER_DAYS):
    """Delete passive-mm-dd-yyyy folders older than keep_days whose files were all imported. Returns their names."""
    searches_dir = Path(searches_dir)
    if not searches_dir.is_dir():
        return []
    today = today or date.today()
    imported = _load_imported(searches_dir)
    removed = []
    for folder in sorted(searches_dir.iterdir()):
        match = _FOLDER_DATE.match(folder.name) if folder.is_dir() else None
        if not match:
            continue
        month, day, year = (int(part) for part in match.groups())
        try:
            folder_day = date(year, month, day)
        except ValueError:
            continue
        if today - folder_day <= timedelta(days=keep_days):
            continue
        files = list(folder.rglob("jobs.json"))
        if all(imported.get(path.relative_to(searches_dir).as_posix()) == path.stat().st_mtime for path in files):
            shutil.rmtree(folder)
            for path in files:
                imported.pop(path.relative_to(searches_dir).as_posix(), None)
            removed.append(folder.name)
    if removed:
        (searches_dir / IMPORTED_FILE).write_text(json.dumps(imported, indent=1, sort_keys=True), encoding="utf-8")
    return removed


def read_jobs(path):
    """(site, jobs) from a capture file; jobs without a title or link are left out."""
    data = _read_capture(path)
    if data is None:
        return None, []
    jobs = []
    for job in data["jobs"]:
        if not isinstance(job, dict):
            continue
        url = canonical_url(str(job.get("url") or ""))
        title = _BADGE.sub("", str(job.get("title") or "")).strip()
        if url and title:
            jobs.append(dict(job, url=url, title=title))
    return data["site"], jobs


# ---------- matching the same job across sites ----------

def company_key(name):
    text = _COMPANY_SUFFIX.sub(" ", str(name or "").casefold())
    return " ".join(re.findall(r"[a-z0-9]+", text))


def title_key(title):
    """The title's words, keeping level words ("Senior Web Designer" is not "Web Designer"); work-place notes dropped."""
    text = _ARRANGEMENT_NOTE.sub(" ", str(title or "").casefold())
    text = re.sub(r"\bfront[ -]?end\b", "frontend", text)
    return " ".join(word for word in re.findall(r"[a-z0-9]+", text) if word not in _TITLE_NOISE)


def city_key(location):
    """The city part of a location such as "Austin, TX (Hybrid)"; empty for remote or state-only places."""
    text = _ARRANGEMENT_NOTE.sub(" ", str(location or "")).strip()
    if not text or "," not in text or re.search(r"\bremote\b|united states", text, re.I):
        return ""
    return " ".join(re.findall(r"[a-z0-9]+", text.split(",")[0].casefold()))


def match_key(company, title, location, arrangement):
    """What identifies one real job across sites, or None when there is too little to be sure.

    Remote jobs match on company and title; others also need the same city.
    """
    who, what = company_key(company), title_key(title)
    if not who or not what:
        return None
    if str(arrangement or "").casefold() == "remote":
        return (who, what, "remote")
    city = city_key(location)
    return (who, what, city) if city else None


def merge_details(old, new):
    """listing_details for a re-imported job: new values win, but an opened job keeps its description."""
    merged = dict(old or {})
    for key, value in (new or {}).items():
        if value in (None, "", []) and merged.get(key):
            continue
        merged[key] = value
    if _LEVELS.get((old or {}).get("capture_level"), 0) > _LEVELS.get((new or {}).get("capture_level"), 0):
        merged["capture_level"] = old["capture_level"]
        if old.get("description"):
            merged["description"] = old["description"]
    if (old or {}).get("also_on"):
        merged["also_on"] = old["also_on"]
    return merged


def add_also_on(details, site, url):
    """Add another site's link to a job's listing_details; False when it was already there."""
    links = list(details.get("also_on") or [])
    if any(link.get("url") == url for link in links if isinstance(link, dict)):
        return False
    links.append({"site": SITES.get(site, (site,))[0], "url": url})
    details["also_on"] = links
    return True


def page_html(job):
    """A small page built from the captured fields, for the location and U.S. checks that read page text."""
    parts = [f"<h1>{escape(job.get('title') or '')}</h1>", f"<p>{escape(job.get('company') or '')}</p>"]
    if job.get("location"):
        parts.append(f"<p class=\"job-location\">{escape(job['location'])}</p>")
    if job.get("description"):
        parts.append(f"<div>{escape(job['description'])}</div>")
    return "<html><body>" + "".join(parts) + "</body></html>"
