import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PAGES = {"index": "Search", "user-dashboard": "Dashboard", "rejected-listings": "Settings", "tuning": "Tuning",
         "credibility-scores": "Credibility Scores"}


class PageTitles(unittest.TestCase):
    def test_every_page_is_titled_page_name_bar_job_finder(self):
        for page, name in PAGES.items():
            html = (ROOT / "templates" / f"{page}.html").read_text(encoding="utf-8")
            self.assertEqual(re.search(r"<title>([^<]*)</title>", html).group(1), f"{name} | Job Finder", page)


if __name__ == "__main__":
    unittest.main()
