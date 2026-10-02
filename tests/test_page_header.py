import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

TEMPLATE = Path(__file__).resolve().parents[1] / "templates" / "index.html"


class PageHeader(unittest.TestCase):
    def test_the_version_is_shown_under_the_title_on_the_job_finder_page(self):
        html = TEMPLATE.read_text(encoding="utf-8")
        self.assertRegex(html, re.compile(r"<h1>\s*Job Finder\s*</h1>\s*<p class=\"app-version\">Version \{\{ app_version \}\}</p>"))


if __name__ == "__main__":
    unittest.main()
