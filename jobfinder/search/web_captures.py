"""Importing the jobs the Web Job Scraper extension saved from the pages you browsed (LinkedIn and others)."""

from datetime import datetime, timezone
import json
import re

from mysql.connector import Error

from jobfinder.profiles.profile_tools import listing_skills
from jobfinder.records.capture_import import (
    CAPTURE_MARK,
    IMPORT_ERRORS,
    SITES as CAPTURE_SITES,
    add_also_on,
    capture_dirs,
    files_to_import,
    mark_imported,
    match_key,
    merge_details,
    page_html as capture_page_html,
    read_jobs,
    remove_old_folders,
    unreadable_files,
)
from jobfinder.records.job_retention import CLOSED_KEEP_DAYS, tidy_closed_jobs
from jobfinder.search import geo
from jobfinder.search import judging
from jobfinder.search import relevance
from jobfinder.search import shared
from jobfinder.search import storage
from jobfinder.search.company_names import company_board_posting, tidy_company_name, verification_label
from jobfinder.search.relevance import expand_job_titles, reject_irrelevant_row
from jobfinder.search.shared import (
    BASE_DIR,
    BLOCKED_COMPANIES,
    CAREER_CREDIBILITY_THRESHOLD,
    is_internship,
    related_family_titles,
    settings,
)
from jobfinder.search.usa_location import US_STATES, detect_work_arrangement, find_state_from_text, selected_state_codes
from jobfinder.sources.job_listings import canonical_url, matching_title as matching_job_title


def load_profile_filters(database):
    """(job titles, cities, state, error) from the saved profile only, so imports don't change with each search."""
    titles, cities, state, problem = [], [], "", None
    cursor = None
    try:
        cursor = database.cursor()
        cursor.execute("SELECT job_title FROM user_profile_job_titles WHERE profile_id = 1 ORDER BY id")
        titles = [row[0].strip() for row in cursor.fetchall() if row[0] and row[0].strip()]
        cursor.execute("SELECT city, radius_miles FROM user_profile_cities WHERE profile_id = 1 ORDER BY id")
        cities = [{"city": row[0], "radius": row[1]} for row in cursor.fetchall() if row[0]]
        cursor.execute("SELECT state FROM user_profile WHERE id = 1")
        row = cursor.fetchone()
        state = (row[0] if row else "") or ""
    except Error as error:
        problem = str(error)
    finally:
        if cursor is not None:
            cursor.close()
    return titles, cities, state, problem


def site_location_in_us(location):
    """The job site's own location field names a U.S. state or the United States ("Austin, TX", "United States (Remote)")."""
    text = str(location or "")
    return bool(find_state_from_text(text) or re.search(r"united states|(?<![A-Za-z])USA?(?![A-Za-z])", text, re.I))


def _row_details(row):
    try:
        details = json.loads(row.get("listing_details") or "{}")
    except (TypeError, ValueError):
        details = {}
    return details if isinstance(details, dict) else {}


class CaptureImport:
    """One --import-captures run: the profile filters and the rows already saved, shared by every job."""

    def __init__(self, database):
        self.database = database
        titles, cities, self.state, self.profile_error = load_profile_filters(database)
        self.wanted = expand_job_titles(titles) if titles else []
        self.wanted += [title for title in related_family_titles(titles)
                        if title.casefold() not in {wanted.casefold() for wanted in self.wanted}]
        self.statewide = {US_STATES[str(item["city"]).strip().casefold()] for item in cities
                          if str(item["city"]).strip().casefold() in US_STATES}
        self.selected_states = selected_state_codes(self.state, cities, self.statewide)
        self.city_targets = geo.prepare_city_targets(database, self.state, cities)
        self.rejected = storage.rejected_posting_urls(database)
        self.by_url = {}
        self.by_key = {}
        with database.cursor(dictionary=True) as cursor:
            cursor.execute("""SELECT id, name, career_job_title, work_arrangement, city, state, source_url, source_type,
                                     listing_details, is_kept
                              FROM companies WHERE is_rejected = 0""")
            for row in cursor.fetchall():
                self._remember(row)

    def _remember(self, row):
        details = _row_details(row)
        if row.get("source_url"):
            self.by_url[canonical_url(row["source_url"])] = row
        place = details.get("location") or ", ".join(part for part in (row.get("city"), row.get("state")) if part)
        key = match_key(row.get("name"), row.get("career_job_title"), place, row.get("work_arrangement"))
        if key:
            self.by_key.setdefault(key, row)

    def _saved_row(self, url):
        with self.database.cursor(dictionary=True) as cursor:
            cursor.execute("""SELECT id, name, career_job_title, work_arrangement, city, state, source_url, source_type,
                                     listing_details, is_kept FROM companies WHERE source_url = %s LIMIT 1""", (url,))
            return cursor.fetchone()

    def _set_details(self, row_id, details):
        with self.database.cursor() as cursor:
            cursor.execute("UPDATE companies SET listing_details = %s WHERE id = %s", (json.dumps(details), row_id))
        self.database.commit()

    def _mark_closed(self, row_id):
        now = datetime.now(timezone.utc)
        with self.database.cursor() as cursor:
            cursor.execute("""UPDATE companies SET job_open_status = 'Closed', last_checked = %s,
                              result_updated_at = CASE WHEN job_open_status <> 'Closed' THEN %s ELSE result_updated_at END
                              WHERE id = %s""", (now, now, row_id))
        self.database.commit()

    def _mark_applied(self, row_id):
        """Saved, with status Applied. A status further along (Interview) is kept; a rejected row comes back."""
        with self.database.cursor() as cursor:
            cursor.execute("""UPDATE companies
                              SET is_kept = 1, is_rejected = 0, rejection_reason = NULL, rejected_at = NULL,
                                  application_status = CASE WHEN application_status IN ('None', 'Saved', 'Rejected')
                                                            THEN 'Applied' ELSE application_status END
                              WHERE id = %s""", (row_id,))
        self.database.commit()

    def company_site(self, company, title, url, location, details, site_domain):
        """The company's own website for a captured job: (domain, View link). Looked up once per job, the way
        web-search leads from other job boards are: a domain guessed from the company name and checked to be that
        company, its careers page, this job on it, or the job on the company's hiring board (Greenhouse, Lever, ...).
        Only the company's sites are visited, never LinkedIn. Updates details in place."""
        if company and not details.get("employer_checked"):
            details["employer_checked"] = True
            print(f"Looking for {company}'s own website...", flush=True)
            notes = []
            employer = relevance.find_employer_site(company, title, url, "", self.wanted or [title], location_hint=location,
                                          allow_search=False, notes=notes)
            if employer:
                details["employer_site"] = {
                    "domain": employer["domain"], "careers_url": employer["careers_url"], "method": employer["method"],
                    "posting_found": bool(employer["posting_url"]), "posting_url": employer["posting_url"],
                    "evidence": list(employer["evidence"]),
                }
                print(f"Company website: {employer['domain']} ({employer['method']})"
                      + (f" — this job is there: {employer['posting_url']}" if employer["posting_url"] else ""))
            else:
                print("Company website not found: " + "; ".join(notes or ["no candidates"]))
            if not (employer and employer["posting_url"]):
                board = company_board_posting(company, title)
                if board:
                    details["ats_posting"] = {"system": board["system"], "url": board["url"]}
                    print(f"This job is on the company's own {board['system'].title()} board: {board['url']}")
        site = details.get("employer_site") or {}
        board = details.get("ats_posting") or {}
        # Re-imports rebuild the evidence list, so the company-site findings are added back from what was stored.
        extra = list(site.get("evidence") or [])
        if board:
            extra.append(f"posting found on the company's own {str(board.get('system', '')).title()} board")
        details["evidence"] = list(dict.fromkeys(list(details.get("evidence") or []) + extra))
        if site.get("domain"):
            details["original_source"] = url
        return site.get("domain") or site_domain, site.get("posting_url") or board.get("url") or url

    def assess(self, job, arrangement):
        """judging.assess_opening's result for a captured job, or a plain pass for one without a location."""
        location = str(job.get("location") or "").strip()
        if not location:
            return {"skip": None, "arrangement": arrangement, "location": {"score": 0}, "detail_score": CAREER_CREDIBILITY_THRESHOLD,
                    "country": None, "state_name": None, "remote_limited_to": set(), "city": None, "lat": None, "lon": None,
                    "miles": None}
        opening = {"title": job["title"], "company": job.get("company") or "", "description": job.get("description") or "",
                   "type": arrangement, "location": location, "locations": [location], "remote_states": [],
                   "schedule": "", "evidence": []}
        outcome = judging.assess_opening(self.database, opening, capture_page_html(job), job["url"], self.state,
                                 self.selected_states, self.statewide, self.city_targets)
        # A card has no page text to prove the job is in the U.S.; the site's own location field is trusted instead.
        if outcome["skip"] == "US eligibility unverified" and site_location_in_us(location):
            outcome["skip"] = None
            outcome["country"] = "United States"
            outcome["location"]["score"] = max(outcome["location"]["score"], shared.USA_CREDIBILITY_THRESHOLD)
        return outcome

    def import_job(self, site, job):
        """Filter and save one captured job. Returns (result, reason) where result is added, updated, linked,
        rejected or skipped.

        A job you already applied to skips the filters: it is always saved, with status Applied."""
        source_type, domain = CAPTURE_SITES[site]
        url, title = job["url"], job["title"]
        company = tidy_company_name(job.get("company")) or None
        applied = bool(job.get("applied"))
        row = self.by_url.get(url)
        if not applied:
            if not row and url in self.rejected:
                return "skipped", "Previously rejected"
            if company and company.casefold() in BLOCKED_COMPANIES:
                return "skipped", "Blocked company"
            if not self.wanted or not matching_job_title(title, self.wanted):
                return "skipped", "Title matches none of your profile's job titles"
            if is_internship(title):
                return "skipped", "Internship"
        closed = bool(job.get("closed"))
        location = str(job.get("location") or "").strip()
        arrangement = job.get("work_arrangement") or detect_work_arrangement(title, location) or None
        outcome = self.assess(job, arrangement)
        if outcome["skip"] and not applied:
            details = _row_details(row) if row else {}
            # A job imported earlier as "Location unknown" is filtered again now that its location is known.
            if row and details.get("location_unknown") and not row.get("is_kept"):
                if reject_irrelevant_row(self.database, row["id"], "wrong_location"):
                    self.by_url.pop(url, None)
                    return "rejected", outcome["skip"]
            return "skipped", outcome["skip"]
        arrangement = outcome["arrangement"] or arrangement

        if not row:
            key = match_key(company, title, location, arrangement)
            same_job = self.by_key.get(key) if key else None
            if same_job:
                details = _row_details(same_job)
                if add_also_on(details, site, url):
                    self._set_details(same_job["id"], details)
                    same_job["listing_details"] = json.dumps(details)
                if applied:
                    self._mark_applied(same_job["id"])
                return "linked", (f"same job as #{same_job['id']} ({same_job.get('source_type') or 'saved'})"
                                  + ("; marked Applied" if applied else ""))
            if closed and not applied:
                return "skipped", "No longer accepting applications"

        matched_title = next((wanted for wanted in self.wanted if matching_job_title(title, [wanted])), None)
        new_details = {
            "captured_by": CAPTURE_MARK, "site": source_type, "external_id": str(job.get("job_id") or ""),
            "capture_level": job.get("level") or "seen", "page_kind": job.get("page_kind") or "other",
            "location": location, "salary": job.get("salary") or "", "posted": job.get("posted") or "",
            "description": str(job.get("description") or "")[:20000], "closes": str(job.get("closes") or "")[:10], "matched_title": matched_title,
            "location_unknown": not location, "remote_limited_to": sorted(outcome["remote_limited_to"]),
            "evidence": [f"Seen on {source_type}", "you applied" if applied else "matching title"],
        }
        details = merge_details(_row_details(row) if row else {}, new_details)
        domain, career_url = self.company_site(company, title, url, location, details, domain)
        details["verification"] = verification_label(details, source_type, company, url)
        inserted = storage.save_company(
            self.database, company, title, outcome["detail_score"], domain, career_url, url, outcome["country"],
            str(outcome["state_name"] or "")[:100] or None, outcome["location"]["score"],
            city=outcome["city"], latitude=outcome["lat"], longitude=outcome["lon"], distance_miles=outcome["miles"],
            work_arrangement=arrangement, skills=listing_skills(details.get("description") or ""),
            source_type=source_type, listing_details=details)
        saved = self._saved_row(url)
        if saved:
            self._remember(saved)
            if closed:
                self._mark_closed(saved["id"])
            if applied:
                self._mark_applied(saved["id"])
        notes = [note for note, on in (("you applied: saved as Applied", applied), ("closed on the site", closed)) if on]
        return ("added" if inserted else "updated"), "; ".join(notes)


def import_captures():
    """--import-captures: filter and save the jobs in web-job-scraper\\searches that changed since the last import."""
    # Like Refresh, the import runs without the search engine: page downloads (the company-website check) must not
    # wait for SearXNG, which is not running.
    shared.update_existing_mode = True
    print()
    print("================================")
    print("     IMPORT CAPTURED JOBS")
    print("================================")
    print()
    _, searches_dir = capture_dirs(settings, BASE_DIR)
    for path in unreadable_files(searches_dir):
        print(f"[{IMPORT_ERRORS['bad_file']}] Skipped a capture file that could not be read: {path}")
    paths = files_to_import(searches_dir)
    work = []
    for path in paths:
        site, jobs = read_jobs(path)
        work.extend((path, site, job) for job in jobs)
    print(f"Capture files to import: {len(paths)} ({len(work)} jobs) from {searches_dir}")
    if not paths:
        print("Stop reason: Nothing new to import.")
        return

    def stop(key, message):
        print(f"Stop reason: [{IMPORT_ERRORS[key]}] Import stopped: {message}")

    database = storage.connect_database()
    if database is None:
        stop("database", "the database could not be reached. Is MySQL (XAMPP) running?")
        return
    if not storage.ensure_database_schema(database):
        database.close()
        stop("schema", "the database could not be updated.")
        return
    counts = {"added": 0, "updated": 0, "linked": 0, "rejected": 0, "skipped": 0}
    failed = 0
    stopped = False
    try:
        run = CaptureImport(database)
        if run.profile_error:
            stop("profile", f"your profile could not be read ({run.profile_error}).")
            return
        if not run.wanted:
            stop("no_titles", "add job titles to your profile first.")
            return
        print("Matching job titles: " + ", ".join(run.wanted))
        for number, (path, site, job) in enumerate(work, start=1):
            if shared.stop_requested():
                stopped = True
                break
            print(f"Importing {number}/{len(work)}: {job['title'][:70]} ({CAPTURE_SITES[site][0]})", flush=True)
            try:
                result, reason = run.import_job(site, job)
            except Error as error:
                database.rollback()
                failed += 1
                result, reason = "skipped", f"[{IMPORT_ERRORS['save']}] could not be saved: {error}"
            counts[result] += 1
            if result == "skipped":
                print(f"Skipped ({reason}): {job['title'][:70]} {job['url']}")
            else:
                print(f"{result.title()}: {job['title'][:70]}" + (f" — {reason}" if reason else ""))
        if not stopped and not failed:
            try:
                mark_imported(searches_dir, paths)
                removed = remove_old_folders(searches_dir)
                if removed:
                    print(f"Removed {len(removed)} capture folder(s) older than 30 days: {', '.join(removed)}")
            except OSError as error:
                print(f"[{IMPORT_ERRORS['files']}] Could not record the imported files or remove old folders: {error}")
        try:
            expired = tidy_closed_jobs(database)
            if expired:
                print(f"Deleted {expired} job(s) closed for more than {CLOSED_KEEP_DAYS} days (saved jobs are kept).")
        except Error as error:
            print(f"[{IMPORT_ERRORS['tidy']}] Could not tidy closed jobs: {error}")
    finally:
        database.close()
    print()
    print(f"Import summary: checked {sum(counts.values())}, added {counts['added']}, updated {counts['updated']}, "
          f"linked {counts['linked']}, rejected {counts['rejected']}, skipped {counts['skipped']}")
    linked = f", {counts['linked']} matched jobs already in your list" if counts["linked"] else ""
    # Files with a failed job stay unmarked, so the next import tries them again.
    errors = (f" [{IMPORT_ERRORS['save']}] {failed} job(s) could not be saved and will be tried again next time."
              if failed else "")
    print(f"Stop reason: {'Import stopped early' if stopped else 'Import finished'}: {counts['added']} added, "
          f"{counts['updated']} updated{linked}, {counts['skipped'] + counts['rejected'] - failed} filtered out.{errors}")
