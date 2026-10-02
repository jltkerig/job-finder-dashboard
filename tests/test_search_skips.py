import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import no_database  # noqa: F401  (cuts tests off from the real database)
from jobfinder.records.search_skips import cached_skip, latest_decisions, record_decision


class SearchSkipHistory(unittest.TestCase):
    def test_clear_skip_cached_temporarily_and_success_clears_it(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "search_skips.jsonl"
            records = {}
            url = "https://example.com/blog/web-design"
            record_decision(path, records, "Article or student employment guide", url, "Article")
            self.assertIsNotNone(cached_skip(latest_decisions(path), url))
            tomorrow = datetime.now(timezone.utc) + timedelta(hours=25)
            self.assertIsNone(cached_skip(records, url, tomorrow))
            record_decision(path, records, "Passed", url, "Web Designer")
            self.assertIsNone(cached_skip(latest_decisions(path), url))

    def test_temporary_failure_does_not_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "search_skips.jsonl"
            records = {}
            record_decision(path, records, "Page unavailable", "https://example.com/jobs/1")
            self.assertIsNone(cached_skip(latest_decisions(path), "https://example.com/jobs/1"))


if __name__ == "__main__":
    unittest.main()
