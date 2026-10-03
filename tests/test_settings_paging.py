import re
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import no_database  # noqa: F401  (cuts tests off from the real database)
from jobfinder.web import listing_queries
from jobfinder.web import blocklists
from jobfinder.web import webfiles
import dashboard

EVENTS = {f"https://example.com/job/{n}": {"url": f"https://example.com/job/{n}", "title": f"Skipped page {n}",
                                             "reason": "Outside selected location", "checked_at": "2026-10-02T12:00:00+00:00"}
          for n in range(60)}


class SettingsPaging(unittest.TestCase):
    def page(self, query=""):
        with patch.object(dashboard, "latest_decisions", return_value=EVENTS), \
                patch.object(listing_queries, "get_rejected_companies", return_value=[]), \
                patch.object(blocklists, "get_blocked_domains", return_value=[]), \
                patch.object(blocklists, "get_blocked_companies", return_value=[]):
            return dashboard.app.test_client().get("/rejected-listings" + query, headers={"Host": "127.0.0.1:5000"}).get_data(as_text=True)

    def test_only_one_page_of_skipped_pages_is_sent(self):
        html = self.page()
        self.assertEqual(len(re.findall(r'<li>\s*<div><strong><a href="https://example.com/job/', html)), webfiles.SKIPS_PER_PAGE)
        self.assertIn("60 skipped pages", html)
        self.assertIn("Page 1 of 3", html)
        self.assertIn("skip_page=2", html)

    def test_the_second_page_continues_where_the_first_stopped(self):
        html = self.page("?skip_page=3")
        self.assertEqual(len(re.findall(r'<li>\s*<div><strong><a href="https://example.com/job/', html)), 10)
        self.assertIn("Page 3 of 3", html)
        self.assertIn("data-open-now", html)  # the panel is open so the page is seen

    def test_searching_is_done_on_the_server_and_can_be_cleared(self):
        html = self.page("?skip_q=skipped%20page%207")
        self.assertEqual(len(re.findall(r'<li>\s*<div><strong><a href="https://example.com/job/', html)), 1)
        self.assertIn("1 skipped page match", html)
        self.assertIn(">Clear</a>", html)
        nothing = self.page("?skip_q=zzzz")
        self.assertIn("0 skipped pages", nothing)
        self.assertIn('name="skip_q"', nothing)  # the box stays so the search can be changed

    def test_a_bad_page_number_does_not_break_the_page(self):
        self.assertIn("Page 1 of 3", self.page("?skip_page=abc"))
        self.assertIn("Page 3 of 3", self.page("?skip_page=99"))


if __name__ == "__main__":
    unittest.main()
