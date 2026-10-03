import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import no_database  # noqa: F401  (cuts tests off from the real database)
from jobfinder.web import core
import dashboard

NOW = datetime(2026, 10, 2, 18, 0, 0, tzinfo=timezone.utc)


class FoundTimes(unittest.TestCase):
    def test_saved_utc_times_are_shown_on_the_eastern_clock(self):
        # 17:59 UTC is 1:59 PM in Eastern daylight time: the page used to show it as 5:59 PM
        self.assertEqual(core.local_time(datetime(2026, 10, 2, 17, 59, 48), "%b %d, %Y %I:%M %p"), "Oct 02, 2026 01:59 PM")
        self.assertEqual(core.local_time(datetime(2026, 12, 1, 17, 0, 0)), "Dec 01, 2026 12:00 PM")  # winter: UTC-5
        self.assertEqual(core.local_time(None), "")

    def test_how_long_a_job_has_been_on_the_results(self):
        ago = lambda **delta: core.how_long((NOW - timedelta(**delta)).replace(tzinfo=None), NOW)
        self.assertEqual(ago(seconds=20), "less than a minute")
        self.assertEqual(ago(minutes=1), "1 minute")
        self.assertEqual(ago(minutes=45), "45 minutes")
        self.assertEqual(ago(hours=5, minutes=30), "5 hours")
        self.assertEqual(ago(days=1, hours=2), "1 day")
        self.assertEqual(ago(days=3), "3 days")
        self.assertEqual(ago(days=15), "2 weeks")
        self.assertEqual(core.how_long(None), "")

    def test_the_results_table_has_a_found_column_before_actions(self):
        html = (Path(dashboard.__file__).parent / "templates" / "index.html").read_text(encoding="utf-8")
        self.assertIn("<th>Listing</th><th>Found</th><th>Actions</th>", html)
        self.assertIn('data-label="Found"', html)
        self.assertIn('colspan="13"', html)  # the details row spans every column, including the new one


if __name__ == "__main__":
    unittest.main()
