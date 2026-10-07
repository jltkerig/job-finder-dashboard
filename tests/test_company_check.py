import json
import sys
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import no_database  # noqa: F401  (cuts tests off from the real database)
from jobfinder.search import company_check as cc

POSTING = ("We are looking for a graphic designer to create print and digital materials for our marketing team. "
           "You will design brochures, social media graphics and web banners, and work closely with writers and "
           "product managers to keep our brand consistent across every channel we use. ") * 2
LISTING = {"company": "Acme Widgets Inc.", "title": "Graphic Designer", "location": "Towson, MD", "description": POSTING}


def page(title="Graphic Designer", org="Acme Widgets", body=POSTING, extra=""):
    ld = json.dumps({"@type": "JobPosting", "title": title, "hiringOrganization": {"name": org}})
    return (f'<html><head><title>{title} | Careers</title><script type="application/ld+json">{ld}</script></head>'
            f"<body><h1>{title}</h1><p>Towson, Maryland. Towson MD.</p><p>{body}</p>{extra}</body></html>")


class JudgeTests(unittest.TestCase):
    def test_same_job_on_the_employer_site_matches(self):
        verdict = cc.judge(LISTING, "https://acmewidgets.com/careers/123", page())
        self.assertTrue(verdict["match"])
        self.assertIn("posted by the same employer", verdict["reasons"])

    def test_different_title_never_matches(self):
        self.assertFalse(cc.judge(LISTING, "https://acmewidgets.com/careers/9", page(title="Senior Software Engineer"))["match"])

    def test_broader_title_is_not_the_same_job(self):
        self.assertLess(cc.title_similarity("Designer", "Senior Product Designer II Payments"), 0.6)

    def test_closed_posting_never_matches(self):
        verdict = cc.judge(LISTING, "https://acmewidgets.com/c/1", page(extra="<p>This position has been filled.</p>"))
        self.assertFalse(verdict["match"])

    def test_other_employer_never_matches(self):
        self.assertFalse(cc.judge(LISTING, "https://globex.com/jobs/1", page(org="Globex"))["match"])


class AggregatorTests(unittest.TestCase):
    def test_aggregator_path_naming_the_company_is_not_the_employer(self):
        self.assertFalse(cc.company_in_url("LawnStarter", "https://freehire.me/jobs/analytics-manager-lawnstarter-j4tr7mug"))
        self.assertTrue(cc.company_in_url("LawnStarter", "https://www.lawnstarter.com/careers/42"))

    def test_seniority_alone_is_not_a_title_match(self):
        self.assertLess(cc.title_similarity("Senior Graphic Designer", "Senior Integrated Designer"), 0.6)

    def test_different_role_is_not_the_same_job(self):
        self.assertEqual(cc.title_similarity("Web Designer, eCRM", "Web Developer, eCRM"), 0.0)


class CandidateTests(unittest.TestCase):
    def test_boards_are_skipped_and_ats_kept(self):
        results = [{"url": "https://www.indeed.com/viewjob?jk=1", "title": "Graphic Designer - Acme"},
                   {"url": "https://boards.greenhouse.io/acmewidgets/jobs/5", "title": "Graphic Designer"},
                   {"url": "https://randomblog.com/post", "title": "Graphic Designer tips"}]
        self.assertEqual(cc.candidate_urls(results, LISTING), ["https://boards.greenhouse.io/acmewidgets/jobs/5"])


class CheckListingsTests(unittest.TestCase):
    def test_match_moves_the_link_and_raises_credibility(self):
        row = {"id": 3, "name": "Acme Widgets Inc.", "career_job_title": "Graphic Designer", "career_url": "https://usnlx.com/j/1",
               "source_url": "https://usnlx.com/j/1", "source_type": "National Labor Exchange", "career_credibility": 3,
               "listing_details": json.dumps({"location": "Towson, MD", "verification": "Listed on the National Labor Exchange; " + cc.NOT_CHECKED})}
        cursor = MagicMock()
        cursor.fetchall.return_value = [row]
        database = MagicMock()
        database.cursor.return_value = cursor
        pages = {"https://usnlx.com/j/1": f"<p>{POSTING}</p>", "https://acmewidgets.com/careers/123": page()}
        fetch = lambda url: SimpleNamespace(url=url, text=pages[url]) if url in pages else None
        search = lambda query: [{"url": "https://acmewidgets.com/careers/123", "title": "Graphic Designer"}]
        self.assertEqual(cc.check_listings(database, search, fetch, today=date(2026, 10, 6)), 1)
        sql, params = cursor.execute.call_args_list[-1][0]
        self.assertIn("career_credibility", sql)
        self.assertEqual(params[0], "https://acmewidgets.com/careers/123")
        details = json.loads(params[2])
        self.assertTrue(details["verification"].startswith("Found on the company site"))
        self.assertEqual(details["company_check"], "2026-10-06")

    def test_job_board_listing_is_always_due_and_rejected_when_not_found(self):
        self.assertTrue(cc._due({}, date(2026, 10, 6), "https://remotive.com/remote-jobs/design/x-1"))
        self.assertFalse(cc._due({}, date(2026, 10, 6), "https://www.kobotoolbox.org/join-our-team/frontend-developer"))
        row = {"id": 9, "name": "Acme Widgets Inc.", "career_job_title": "Graphic Designer", "career_url": "https://remotive.com/j/1",
               "source_url": "https://remotive.com/j/1", "source_type": "Remotive", "career_credibility": 6, "is_kept": 0,
               "application_status": "None", "listing_details": "{}"}
        cursor = MagicMock()
        cursor.fetchall.return_value = [row]
        database = MagicMock()
        database.cursor.return_value = cursor
        self.assertEqual(cc.check_listings(database, lambda q: [], lambda u: None, today=date(2026, 10, 6)), 0)
        self.assertIn("rejection_reason = 'not_on_company_site'", cursor.execute.call_args_list[-1][0][0])

    def test_saved_job_board_listing_is_flagged_not_rejected(self):
        row = {"id": 9, "name": "Acme Widgets Inc.", "career_job_title": "Graphic Designer", "career_url": "https://remotive.com/j/1",
               "source_url": "https://remotive.com/j/1", "source_type": "Remotive", "career_credibility": 6, "is_kept": 1,
               "application_status": "Saved", "listing_details": "{}"}
        cursor = MagicMock()
        cursor.fetchall.return_value = [row]
        database = MagicMock()
        database.cursor.return_value = cursor
        cc.check_listings(database, lambda q: [], lambda u: None, today=date(2026, 10, 6))
        self.assertNotIn("is_rejected", cursor.execute.call_args_list[-1][0][0])

    def test_recently_checked_listing_is_skipped(self):
        details = {"company_check": "2026-10-04", "verification": "Listed on X; no matching posting found on the company site"}
        self.assertFalse(cc._due(details, date(2026, 10, 6)))
        self.assertTrue(cc._due(details, date(2026, 10, 12)))


if __name__ == "__main__":
    unittest.main()
