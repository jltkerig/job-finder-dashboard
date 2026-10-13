import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import no_database  # noqa: F401  (cuts tests off from the real database)
from jobfinder.web import applied_dates


class FakeCursor:
    """Holds one job's listing_details and records what was written back."""

    def __init__(self, details=None):
        self.details = json.dumps(details) if details is not None else None
        self.row = None
        self.writes = 0

    def execute(self, sql, params=()):
        if sql.startswith("SELECT"):
            self.row = (self.details,)
        else:
            self.details = params[0]
            self.writes += 1

    def fetchone(self):
        return self.row

    def saved(self):
        return json.loads(self.details or "{}").get("applied_on")


class AppliedDates(unittest.TestCase):
    def setUp(self):
        patcher = patch.object(applied_dates, "today", return_value="2026-10-12")
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_marking_applied_saves_todays_date(self):
        cursor = FakeCursor({"apply_queued": True})
        applied_dates.save(cursor, 1, "Applied")
        self.assertEqual(cursor.saved(), "2026-10-12")
        self.assertTrue(json.loads(cursor.details)["apply_queued"])

    def test_the_date_is_kept_when_marked_applied_again(self):
        cursor = FakeCursor({"applied_on": "2026-10-05"})
        applied_dates.save(cursor, 1, "Applied", previous="Applied")
        self.assertEqual(cursor.saved(), "2026-10-05")
        self.assertEqual(cursor.writes, 0)

    def test_a_job_already_applied_before_dates_existed_is_not_given_todays_date(self):
        cursor = FakeCursor({})
        applied_dates.save(cursor, 1, "Applied", "", previous="Applied")
        self.assertIsNone(cursor.saved())
        applied_dates.save(cursor, 1, "Applied", "2026-09-30", previous="Applied")
        self.assertEqual(cursor.saved(), "2026-09-30")

    def test_a_date_you_change_is_saved_and_kept_for_later_statuses(self):
        cursor = FakeCursor({"applied_on": "2026-10-05"})
        applied_dates.save(cursor, 1, "Applied", "2026-10-08")
        self.assertEqual(cursor.saved(), "2026-10-08")
        applied_dates.save(cursor, 1, "Interview")  # form without a date field
        self.assertEqual(cursor.saved(), "2026-10-08")

    def test_other_statuses_do_not_invent_a_date_and_a_bad_date_is_ignored(self):
        cursor = FakeCursor({})
        applied_dates.save(cursor, 1, "Saved", "not a date")
        self.assertIsNone(cursor.saved())
        applied_dates.save(cursor, 1, "Interview", "")
        self.assertIsNone(cursor.saved())

    def test_clearing_the_date_removes_it_unless_the_job_is_applied(self):
        cursor = FakeCursor({"applied_on": "2026-10-05"})
        applied_dates.save(cursor, 1, "Interview", "")
        self.assertIsNone(cursor.saved())

    def test_saved_jobs_get_a_readable_label(self):
        jobs = applied_dates.add_to([{"listing_details": '{"applied_on": "2026-10-05"}'}, {"listing_details": None}])
        self.assertEqual((jobs[0]["applied_on"], jobs[0]["applied_label"]), ("2026-10-05", "Oct 05, 2026"))
        self.assertEqual((jobs[1]["applied_on"], jobs[1]["applied_label"]), ("", ""))


if __name__ == "__main__":
    unittest.main()
