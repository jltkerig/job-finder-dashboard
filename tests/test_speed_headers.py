import gzip
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import no_database  # noqa: F401  (cuts tests off from the real database)
import dashboard


class SpeedHeaders(unittest.TestCase):
    def setUp(self):
        self.client = dashboard.app.test_client()

    def get(self, path, **headers):
        return self.client.get(path, headers={"Host": "127.0.0.1:5000", **headers})

    def test_the_stylesheet_is_sent_gzipped_when_the_browser_accepts_it(self):
        plain = self.get("/static/css/style.css")
        packed = self.get("/static/css/style.css?v=1", **{"Accept-Encoding": "gzip, deflate"})
        self.assertEqual(packed.headers["Content-Encoding"], "gzip")
        self.assertEqual(gzip.decompress(packed.data), plain.data)
        self.assertLess(len(packed.data), len(plain.data) / 3)

    def test_nothing_is_compressed_for_a_browser_that_does_not_ask(self):
        self.assertNotIn("Content-Encoding", self.get("/static/css/style.css").headers)

    def test_versioned_static_files_are_kept_by_the_browser(self):
        self.assertIn("max-age=31536000", self.get("/static/css/style.css?v=1.1.149").headers["Cache-Control"])
        self.assertNotIn("max-age=31536000", self.get("/static/css/style.css").headers.get("Cache-Control", ""))

    def test_pages_link_the_stylesheet_with_the_version(self):
        for page in ("index", "user-dashboard", "tuning", "rejected-listings", "credibility-scores"):
            html = (Path(__file__).resolve().parents[1] / "templates" / f"{page}.html").read_text(encoding="utf-8")
            self.assertIn('/static/css/style.css?v={{ app_version }}', html, page)


if __name__ == "__main__":
    unittest.main()
