"""Tests with made-up data only. Every folder is temporary; the database is never touched."""

import sys

import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_resume_builder  # noqa: E402,F401  (sets the temporary folders before the app's config is read)
import jobfinder_db  # noqa: E402
import paging  # noqa: E402


class Pages(unittest.TestCase):
    def test_twenty_five_to_a_page(self):
        items = list(range(60))
        first, page, pages = paging.paginate(items, 1)
        self.assertEqual((first, page, pages), (list(range(25)), 1, 3))
        last, page, pages = paging.paginate(items, 3)
        self.assertEqual((last, page, pages), (list(range(50, 60)), 3, 3))

    def test_a_page_outside_the_list_is_moved_back_inside(self):
        self.assertEqual(paging.paginate(list(range(30)), 9)[1:], (2, 2))
        self.assertEqual(paging.paginate(list(range(30)), 0)[1], 1)
        self.assertEqual(paging.paginate(list(range(30)), None)[1], 1)

    def test_an_empty_list_has_one_empty_page(self):
        self.assertEqual(paging.paginate([], 1), ([], 1, 1))

    def test_exactly_a_full_page_is_one_page(self):
        self.assertEqual(paging.paginate(list(range(25)), 1)[2], 1)
        self.assertEqual(paging.paginate(list(range(26)), 1)[2], 2)

    def test_links_skip_the_middle_of_a_long_list(self):
        self.assertEqual(paging.page_links(1, 3), [1, 2, 3])
        self.assertEqual(paging.page_links(10, 20), [1, None, 8, 9, 10, 11, 12, None, 20])
        self.assertEqual(paging.page_links(1, 20), [1, 2, 3, None, 20])
        self.assertEqual(paging.page_links(1, 1), [1])


class SavedListOnThePage(unittest.TestCase):
    def page(self, query):
        import app as builder
        records = [{"kind": "resume", "pdf": f"Pat_Test_{n:03d}.pdf", "job_title": f"Job {n}", "company": "Acme",
                    "updated": f"2026-10-01T10:{n % 60:02d}:00", "pdf_exists": True} for n in range(60)]
        with mock.patch.object(builder.builds, "list_builds", return_value=records), \
                mock.patch.object(jobfinder_db, "get_profile", side_effect=jobfinder_db.JobFinderUnavailable("offline")):
            return builder.app.test_client().get("/" + query).get_data(as_text=True)

    def test_the_first_page_shows_25_and_links_to_the_rest(self):
        html = self.page("")
        self.assertIn("Pat_Test_000.pdf", html)
        self.assertIn("Pat_Test_024.pdf", html)
        self.assertNotIn("Pat_Test_025.pdf", html)
        self.assertIn("Showing 1–25 of 60", html)
        self.assertIn("?page=2#saved-builds", html)

    def test_the_last_page_shows_the_rest(self):
        html = self.page("?page=3")
        self.assertIn("Pat_Test_050.pdf", html)
        self.assertIn("Pat_Test_059.pdf", html)
        self.assertNotIn("Pat_Test_049.pdf", html)
        self.assertIn("Showing 51–60 of 60", html)


if __name__ == "__main__":
    unittest.main()
