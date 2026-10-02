import json
import os
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import no_database  # noqa: F401  (cuts tests off from the real database)
import capture_import as capture
import job_finder as finder
import job_retention


def capture_file(root, site="linkedin", jobs=None, day="passive-09-30-2026"):
    """A capture file as the extension writes it, under root/<day>/<site>/jobs.json."""
    path = Path(root) / day / site / "jobs.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"source": "web-job-scraper", "site": site, "jobs": jobs or []}), encoding="utf-8")
    return path


def job(**fields):
    base = {"site": "linkedin", "job_id": "4012345678", "url": "https://www.linkedin.com/jobs/view/4012345678",
            "title": "Web Designer", "company": "Acme Corp", "location": "Austin, TX", "work_arrangement": "",
            "salary": "", "posted": "", "description": "", "level": "seen", "closed": False, "page_kind": "search"}
    base.update(fields)
    return base


class CaptureFiles(unittest.TestCase):
    def test_waiting_files_are_moved_with_a_csv_and_imported_once(self):
        with tempfile.TemporaryDirectory() as downloads, tempfile.TemporaryDirectory() as searches:
            waiting = Path(downloads) / "searches"
            capture_file(waiting, jobs=[job()])
            (waiting / "notes.json").write_text("{}", encoding="utf-8")
            pending = capture.pending_files(downloads)
            self.assertEqual([item["relative"] for item in pending], ["passive-09-30-2026/linkedin/jobs.json"])
            self.assertEqual(pending[0]["jobs"], 1)

            moved = capture.move_pending(downloads, searches)
            target = Path(searches) / "passive-09-30-2026" / "linkedin" / "jobs.json"
            self.assertEqual(moved, [target])
            self.assertTrue(target.with_name("jobs.csv").read_text(encoding="utf-8-sig").startswith("site,job_id,title"))
            self.assertEqual(capture.pending_files(downloads), [])

            self.assertEqual(capture.files_to_import(searches), [target])
            capture.mark_imported(searches, [target])
            self.assertEqual(capture.files_to_import(searches), [])
            # A newer copy of the day's file (more jobs captured later) replaces it and is imported again.
            capture_file(waiting, jobs=[job(), job(job_id="2", url="https://www.linkedin.com/jobs/view/2")])
            capture.move_pending(downloads, searches)
            os.utime(target, (target.stat().st_atime, target.stat().st_mtime + 5))
            self.assertEqual(capture.files_to_import(searches), [target])
            self.assertEqual(len(capture.read_jobs(target)[1]), 2)

    def test_old_folders_are_removed_only_after_import(self):
        with tempfile.TemporaryDirectory() as searches:
            old = capture_file(searches, day="passive-08-01-2026")
            recent = capture_file(searches, day="passive-09-20-2026")
            self.assertEqual(capture.remove_old_folders(searches, today=date(2026, 9, 30)), [])
            capture.mark_imported(searches, [old, recent])
            self.assertEqual(capture.remove_old_folders(searches, today=date(2026, 9, 30)), ["passive-08-01-2026"])
            self.assertFalse(old.exists())
            self.assertTrue(recent.exists())

    def test_jobs_without_a_title_or_web_link_are_left_out(self):
        with tempfile.TemporaryDirectory() as folder:
            path = capture_file(folder, jobs=[job(), job(title=""), job(url="javascript:alert(1)"),
                                              job(job_id="9", url="https://www.linkedin.com/jobs/view/9",
                                                  title="UX Designer (Verified job)")])
            site, jobs = capture.read_jobs(path)
            self.assertEqual(site, "linkedin")
            self.assertEqual([item["title"] for item in jobs], ["Web Designer", "UX Designer"])

    def test_maryland_workforce_exchange_captures_are_read(self):
        with tempfile.TemporaryDirectory() as folder:
            url = "https://mwejobs.maryland.gov/vosnet/jobbanks/jobdetails.aspx?enc=AAA"
            path = capture_file(folder, site="mwe", jobs=[job(site="mwe", job_id="1a2b3c4d", url=url, title="Graphic Designer")])
            site, jobs = capture.read_jobs(path)
            self.assertEqual(site, "mwe")
            self.assertEqual(jobs[0]["title"], "Graphic Designer")
        self.assertIn("Maryland Workforce Exchange", capture.CAPTURE_SOURCES)  # never re-fetched by Refresh

    def test_other_json_files_are_not_capture_files(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "jobs.json"
            path.write_text(json.dumps({"site": "linkedin", "jobs": []}), encoding="utf-8")
            self.assertEqual(capture.read_jobs(path), (None, []))


class DownloadFolder(unittest.TestCase):
    def test_the_desktop_is_used_when_firefox_saves_there(self):
        with tempfile.TemporaryDirectory() as home:
            self.assertEqual(capture.default_download_dir(home), Path(home) / "Downloads" / "web-job-scraper")
            (Path(home) / "Desktop" / "web-job-scraper").mkdir(parents=True)
            self.assertEqual(capture.default_download_dir(home), Path(home) / "Desktop" / "web-job-scraper")
            (Path(home) / "Downloads" / "web-job-scraper").mkdir(parents=True)
            self.assertEqual(capture.default_download_dir(home), Path(home) / "Downloads" / "web-job-scraper")


class Matching(unittest.TestCase):
    def test_the_same_job_on_two_sites_has_one_key(self):
        linkedin = capture.match_key("Acme Corp", "Senior Web Designer", "Austin, TX (Hybrid)", "Hybrid")
        self.assertIsNotNone(linkedin)
        self.assertEqual(linkedin, capture.match_key("Acme, Inc.", "Senior Web Designer", "Austin, Texas", "Hybrid"))
        self.assertNotEqual(linkedin, capture.match_key("Acme", "Web Designer", "Austin, TX", "Hybrid"))
        self.assertNotEqual(linkedin, capture.match_key("Acme", "Senior Web Designer", "Dallas, TX", "Hybrid"))

    def test_remote_jobs_match_without_a_city_and_others_need_one(self):
        self.assertEqual(capture.match_key("Acme", "UX Designer", "United States (Remote)", "Remote"),
                         capture.match_key("Acme LLC", "UX Designer", "", "Remote"))
        self.assertIsNone(capture.match_key("Acme", "UX Designer", "Texas", "On-site"))
        self.assertIsNone(capture.match_key("", "UX Designer", "Austin, TX", "On-site"))

    def test_an_opened_job_keeps_its_description_when_seen_again(self):
        old = {"capture_level": "opened", "description": "Full text", "also_on": [{"site": "Indeed", "url": "u"}]}
        merged = capture.merge_details(old, {"capture_level": "seen", "description": "", "salary": "$80K"})
        self.assertEqual(merged["capture_level"], "opened")
        self.assertEqual(merged["description"], "Full text")
        self.assertEqual(merged["salary"], "$80K")
        self.assertEqual(merged["also_on"], [{"site": "Indeed", "url": "u"}])

    def test_also_on_links_are_added_once(self):
        details = {}
        self.assertTrue(capture.add_also_on(details, "linkedin", "https://www.linkedin.com/jobs/view/1"))
        self.assertFalse(capture.add_also_on(details, "linkedin", "https://www.linkedin.com/jobs/view/1"))
        self.assertEqual(details["also_on"], [{"site": "LinkedIn", "url": "https://www.linkedin.com/jobs/view/1"}])


def import_run(rows=()):
    """A CaptureImport without a database: the profile wants Web Designer jobs in Texas."""
    run = object.__new__(finder.CaptureImport)
    run.database = None
    run.state, run.wanted = "TX", ["Web Designer"]
    run.statewide, run.selected_states, run.city_targets = set(), {"TX"}, []
    run.rejected, run.by_url, run.by_key = set(), {}, {}
    for row in rows:
        run._remember(row)
    return run


def passing(**changes):
    outcome = {"skip": None, "arrangement": "On-site", "location": {"score": 3}, "detail_score": 3, "country": "United States",
               "state_name": "TX", "remote_limited_to": set(), "city": "Austin", "lat": 30.2, "lon": -97.7, "miles": 4.0}
    outcome.update(changes)
    return outcome


class ImportFilter(unittest.TestCase):
    def setUp(self):
        self.saved = []
        # The company-website lookups go to the internet; tests decide what they find.
        self.employer = None
        self.board = None
        for name, value in (("save_company", lambda *args, **kwargs: self.saved.append((args, kwargs)) or True),
                            ("find_employer_site", lambda *args, **kwargs: self.employer),
                            ("company_board_posting", lambda *args, **kwargs: self.board)):
            patcher = patch.object(finder, name, side_effect=value)
            self.lookups = getattr(self, "lookups", {})
            self.lookups[name] = patcher.start()
            self.addCleanup(patcher.stop)

    def import_one(self, run, captured, outcome=None, saved_row=None):
        """(result, assess mock, _set_details mock) for one captured job; nothing touches a database.
        self.applied_ids collects the rows marked Saved + Applied."""
        self.applied_ids = []
        with patch.object(finder, "assess_opening", return_value=outcome or passing()) as assess, \
                patch.object(run, "_saved_row", return_value=saved_row), \
                patch.object(run, "_set_details") as set_details, patch.object(run, "_mark_closed"), \
                patch.object(run, "_mark_applied", side_effect=self.applied_ids.append):
            result = run.import_job("linkedin", captured)
        return result, assess, set_details

    def test_titles_that_match_nothing_are_skipped(self):
        result, _, _ = self.import_one(import_run(), job(title="Nurse Manager"))
        self.assertEqual(result[0], "skipped")
        self.assertIn("job titles", result[1])
        self.assertEqual(self.saved, [])

    def test_the_sites_location_is_enough_us_proof_for_a_card(self):
        result, _, _ = self.import_one(import_run(), job(), outcome=passing(skip="US eligibility unverified"))
        self.assertEqual(result[0], "added")
        args, kwargs = self.saved[0]
        self.assertEqual(args[6], "https://www.linkedin.com/jobs/view/4012345678")  # source_url
        self.assertEqual(args[9], finder.USA_CREDIBILITY_THRESHOLD)  # usa_credibility
        self.assertEqual(kwargs["source_type"], "LinkedIn")
        self.assertEqual(kwargs["listing_details"]["captured_by"], "web-job-scraper")
        self.assertFalse(kwargs["listing_details"]["location_unknown"])

    def test_a_card_that_names_no_us_place_stays_unverified(self):
        result, _, _ = self.import_one(import_run(), job(location="Toronto, Ontario"),
                                       outcome=passing(skip="US eligibility unverified"))
        self.assertEqual(result, ("skipped", "US eligibility unverified"))

    def test_outside_your_cities_is_skipped(self):
        result, _, _ = self.import_one(import_run(), job(), outcome=passing(skip="Outside selected location"))
        self.assertEqual(result, ("skipped", "Outside selected location"))

    def test_a_job_without_a_location_is_added_as_location_unknown(self):
        result, assess, _ = self.import_one(import_run(), job(location=""))
        assess.assert_not_called()
        self.assertEqual(result[0], "added")
        self.assertTrue(self.saved[0][1]["listing_details"]["location_unknown"])

    def test_the_same_job_already_saved_from_another_source_is_linked(self):
        existing = {"id": 7, "name": "Acme, Inc.", "career_job_title": "Web Designer", "work_arrangement": "On-site",
                    "city": "Austin", "state": "TX", "source_url": "https://careers.acme.com/jobs/12",
                    "source_type": "Employer careers", "listing_details": json.dumps({"location": "Austin, TX"}), "is_kept": 0}
        result, _, set_details = self.import_one(import_run([existing]), job())
        self.assertEqual(result[0], "linked")
        self.assertEqual(self.saved, [])
        row_id, details = set_details.call_args[0]
        self.assertEqual(row_id, 7)
        self.assertEqual(details["also_on"], [{"site": "LinkedIn", "url": "https://www.linkedin.com/jobs/view/4012345678"}])

    def test_a_new_job_the_site_says_is_closed_is_not_added(self):
        result, _, _ = self.import_one(import_run(), job(closed=True, level="opened", description="x" * 200))
        self.assertEqual(result, ("skipped", "No longer accepting applications"))

    def test_a_job_you_applied_to_is_saved_as_applied_even_outside_your_filters(self):
        saved_row = {"id": 42, "name": "Delta", "career_job_title": "Nurse Manager", "work_arrangement": None,
                     "city": None, "state": None, "source_url": "https://www.linkedin.com/jobs/view/4012345678",
                     "source_type": "LinkedIn", "listing_details": "{}", "is_kept": 0}
        result, _, _ = self.import_one(import_run(), job(title="Nurse Manager", company="Delta", applied=True),
                                       outcome=passing(skip="Outside selected location"), saved_row=saved_row)
        self.assertEqual(result, ("added", "you applied: saved as Applied"))
        self.assertEqual(self.applied_ids, [42])
        self.assertIn("you applied", self.saved[0][1]["listing_details"]["evidence"])

    def test_applying_to_a_job_already_in_your_list_marks_that_row(self):
        existing = {"id": 7, "name": "Acme", "career_job_title": "Web Designer", "work_arrangement": "On-site",
                    "city": "Austin", "state": "TX", "source_url": "https://careers.acme.com/jobs/12",
                    "source_type": "Employer careers", "listing_details": json.dumps({"location": "Austin, TX"}), "is_kept": 0}
        result, _, _ = self.import_one(import_run([existing]), job(applied=True))
        self.assertEqual(result[0], "linked")
        self.assertEqual(self.applied_ids, [7])

    def test_the_companys_own_website_is_looked_up_once(self):
        self.employer = {"domain": "acme.com", "careers_url": "https://acme.com/careers", "method": "domain guess",
                         "posting_url": "https://acme.com/careers/web-designer", "evidence": ["employer site: acme.com"]}
        result, _, _ = self.import_one(import_run(), job())
        self.assertEqual(result[0], "added")
        args, kwargs = self.saved[0]
        self.assertEqual(args[4], "acme.com")  # domain: the company's site
        self.assertEqual(args[5], "https://acme.com/careers/web-designer")  # View link: the job on the company site
        self.assertEqual(args[6], "https://www.linkedin.com/jobs/view/4012345678")  # still keyed by the LinkedIn link
        details = kwargs["listing_details"]
        self.assertEqual(details["verification"], "Listed on the company's website")
        self.assertIn("employer site: acme.com", details["evidence"])
        self.lookups["company_board_posting"].assert_not_called()  # already found on the company's site

        # Importing the job again keeps what was found without looking it up again.
        self.lookups["find_employer_site"].reset_mock()
        row = {"id": 3, "name": "Acme Corp", "career_job_title": "Web Designer", "work_arrangement": "On-site",
               "city": "Austin", "state": "TX", "source_url": job()["url"], "source_type": "LinkedIn",
               "listing_details": json.dumps(details), "is_kept": 0}
        result, _, _ = self.import_one(import_run([row]), job(level="opened", description="x" * 200))
        self.assertEqual(result[0], "added")
        self.lookups["find_employer_site"].assert_not_called()
        self.assertEqual(self.saved[1][0][4], "acme.com")
        self.assertIn("employer site: acme.com", self.saved[1][1]["listing_details"]["evidence"])

    def test_the_companys_hiring_board_is_tried_when_its_site_has_no_posting(self):
        self.board = {"system": "greenhouse", "url": "https://boards.greenhouse.io/acme/jobs/1"}
        result, _, _ = self.import_one(import_run(), job())
        args, kwargs = self.saved[0]
        self.assertEqual(args[4], "linkedin.com")
        self.assertEqual(args[5], "https://boards.greenhouse.io/acme/jobs/1")
        self.assertEqual(kwargs["listing_details"]["verification"], "Posted on the company's own Greenhouse hiring board")

    def test_previously_rejected_jobs_stay_out(self):
        run = import_run()
        run.rejected = {"https://www.linkedin.com/jobs/view/4012345678"}
        self.assertEqual(self.import_one(run, job())[0], ("skipped", "Previously rejected"))


class ImportErrorCodes(unittest.TestCase):
    def run_import(self, files, database=None):
        """What import_captures prints for a searches folder holding `files` (name -> text)."""
        import contextlib
        import io

        with tempfile.TemporaryDirectory() as searches:
            for name, text in files.items():
                path = Path(searches) / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text, encoding="utf-8")
            output = io.StringIO()
            with patch.object(finder, "capture_dirs", return_value=(Path(searches), Path(searches))), \
                    patch.object(finder, "connect_database", return_value=database), \
                    contextlib.redirect_stdout(output):
                finder.import_captures()
            return output.getvalue()

    def test_an_unreachable_database_stops_with_E6001(self):
        good = json.dumps({"source": "web-job-scraper", "site": "linkedin", "jobs": [job()]})
        printed = self.run_import({"passive-09-30-2026/linkedin/jobs.json": good})
        self.assertIn("Stop reason: [E6001] Import stopped", printed)

    def test_a_damaged_capture_file_is_reported_with_E6005(self):
        printed = self.run_import({"passive-09-30-2026/linkedin/jobs.json": '{"source": "web-job-scr'})
        self.assertIn("[E6005] Skipped a capture file that could not be read", printed)
        self.assertIn("Stop reason: Nothing new to import.", printed)

    def test_every_import_code_is_distinct(self):
        codes = list(capture.IMPORT_ERRORS.values())
        self.assertEqual(len(codes), len(set(codes)))
        self.assertTrue(all(code.startswith("E6") for code in codes))


class DashboardVisibility(unittest.TestCase):
    def test_captured_jobs_show_although_their_site_is_blocked_for_web_search(self):
        import dashboard

        rows = [
            {"id": 1, "name": "Acme", "domain": "linkedin.com", "source_type": "LinkedIn",
             "source_url": "https://www.linkedin.com/jobs/view/1", "career_url": "https://www.linkedin.com/jobs/view/1"},
            {"id": 2, "name": "Beta", "domain": "linkedin.com", "source_type": "SearXNG",
             "source_url": "https://www.linkedin.com/jobs/view/2", "career_url": "https://www.linkedin.com/jobs/view/2"},
        ]

        class Cursor:
            def execute(self, *args):
                pass

            def fetchall(self):
                return [dict(row) for row in rows]

            def close(self):
                pass

        class Connection:
            def cursor(self, dictionary=False):
                return Cursor()

            def is_connected(self):
                return True

            def close(self):
                pass

        with patch.object(dashboard.mysql.connector, "connect", return_value=Connection()), \
                patch.object(dashboard, "get_blocked_domains", return_value=["linkedin.com"]), \
                patch.object(dashboard, "get_blocked_companies", return_value=[]):
            shown = dashboard.get_companies()
        self.assertEqual([row["id"] for row in shown], [1])


class ClosedJobRetention(unittest.TestCase):
    def test_saved_jobs_are_never_deleted(self):
        calls = []

        class Cursor:
            rowcount = 2

            def execute(self, sql, params=()):
                calls.append((" ".join(sql.split()), params))

        self.assertEqual(job_retention.delete_expired_closed(Cursor(), 30), 2)
        sql, params = calls[0]
        self.assertIn("job_open_status = 'Closed'", sql)
        self.assertIn("is_kept = 0", sql)
        self.assertIn("application_status NOT IN (%s, %s, %s, %s)", sql)
        self.assertEqual(params, (30, "Saved", "Applied", "Talking With Recruiter", "Interview"))


if __name__ == "__main__":
    unittest.main()
