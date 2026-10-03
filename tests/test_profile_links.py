import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import no_database  # noqa: F401  (cuts tests off from the real database)
from jobfinder.web.profile_store import clean_link

ROOT = Path(__file__).resolve().parents[1]


class ProfileLinks(unittest.TestCase):
    def test_links_get_https_and_anything_else_is_dropped(self):
        self.assertEqual(clean_link("linkedin.com/in/example"), "https://linkedin.com/in/example")
        self.assertEqual(clean_link(" https://example.com/ "), "https://example.com/")
        self.assertEqual(clean_link("http://example.com"), "http://example.com")
        self.assertEqual(clean_link("javascript:alert(1)"), "")
        self.assertEqual(clean_link("not a link"), "")
        self.assertEqual(clean_link(""), "")

    def test_the_dashboard_has_both_boxes(self):
        page = (ROOT / "templates" / "user-dashboard.html").read_text(encoding="utf-8")
        self.assertIn('name="linkedin_url" type="text" inputmode="url"', page)
        self.assertIn('name="portfolio_url" type="text" inputmode="url"', page)


if __name__ == "__main__":
    unittest.main()
