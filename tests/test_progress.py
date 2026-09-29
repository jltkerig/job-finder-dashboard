import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import dashboard


class ProgressMessages(unittest.TestCase):
    def test_skip_reason_and_pass_reason_are_shown(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "job_finder.log"
            path.write_text("\n=== 2026-09-29T14:00:00 | search | Web Designer | MD ===\n"
                            "Checking result 1: Article\n"
                            "Skipped (Article or student employment guide): Article https://example.com/a\n",
                            encoding="utf-8")
            with patch.object(dashboard, "SCRAPER_LOG_FILE", path):
                status = dashboard.search_progress_from_log("search")
                self.assertIn("Article or student employment guide", status["progress"])
                self.assertEqual((status["checked"], status["passed"]), (1, 0))
                with path.open("a", encoding="utf-8") as stream:
                    stream.write("Checking result 2: Web Designer\n"
                                 "Passed validation: 1 · Web Designer — JobPosting data, direct listing link\n")
                status = dashboard.search_progress_from_log("search")
                self.assertIn("JobPosting data", status["progress"])
                self.assertEqual((status["checked"], status["passed"]), (2, 1))


if __name__ == "__main__":
    unittest.main()
