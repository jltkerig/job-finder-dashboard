import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import no_database  # noqa: F401  (cuts tests off from the real database)
from jobfinder.web import search_control
from jobfinder.web import webfiles
import dashboard


class ProgressMessages(unittest.TestCase):
    def test_skip_reason_and_pass_reason_are_shown(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "job_finder.log"
            path.write_text("\n=== 2026-09-29T14:00:00 | search | Web Designer | MD ===\n"
                            "Checking result 1: Article\n"
                            "Skipped (Article or student employment guide): Article https://example.com/a\n",
                            encoding="utf-8")
            with patch.object(webfiles, "SCRAPER_LOG_FILE", path):
                status = search_control.search_progress_from_log("search")
                self.assertIn("Article or student employment guide", status["progress"])
                self.assertEqual((status["checked"], status["passed"]), (1, 0))
                with path.open("a", encoding="utf-8") as stream:
                    stream.write("Checking result 2: Web Designer\n"
                                 "Passed validation: 1 · Web Designer — JobPosting data, direct listing link\n")
                status = search_control.search_progress_from_log("search")
                self.assertIn("JobPosting data", status["progress"])
                self.assertEqual((status["checked"], status["passed"]), (2, 1))


class NewVersusAlreadySaved(unittest.TestCase):
    def test_only_new_saves_count_toward_the_limit(self):
        log = chr(10).join([
            "", "=== 2026-09-29T14:00:00 | search | Web Designer | MD ===",
            "Passed validation: 1 · A — x", "Saved job 1/10: https://a.example/1",
            "Passed validation: 2 · B — x",
            "Passed validation: 3 · C — x", "Saved viable company (2/10).",
            "Passed validation: 4 · D — x", ""])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "job_finder.log"
            path.write_text(log, encoding="utf-8")
            with patch.object(webfiles, "SCRAPER_LOG_FILE", path):
                status = search_control.search_progress_from_log("search")
        self.assertEqual((status["passed"], status["saved"], status["limit"]), (4, 2, 10))


class TrackingParameters(unittest.TestCase):
    def test_the_same_job_with_and_without_apply_tracking_is_one_url(self):
        from jobfinder.sources.job_listings import canonical_url
        self.assertEqual(canonical_url("https://www.builtinnyc.com/job/lead-ux/11399021?applyRequired=true"),
                         canonical_url("https://www.builtinnyc.com/job/lead-ux/11399021"))
        self.assertNotEqual(canonical_url("https://x.example/jobs?id=1"), canonical_url("https://x.example/jobs?id=2"))


if __name__ == "__main__":
    unittest.main()
