import json
import sys
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import no_database  # noqa: F401  (cuts tests off from the real database)
from jobfinder.search.web_captures import CaptureImport


class FakeCursor:
    def __init__(self, row):
        self.row = row
        self.statements = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=()):
        self.statements.append((" ".join(sql.split()), params))

    def fetchone(self):
        return self.row


class FakeDatabase:
    def __init__(self, row):
        self.cursor_ = FakeCursor(row)
        self.commits = 0

    def cursor(self, **kwargs):
        return self.cursor_

    def commit(self):
        self.commits += 1


def mark(row):
    run = object.__new__(CaptureImport)
    run.database = FakeDatabase(row)
    run._mark_applied(7)
    return run.database


class AppliedDateOnImport(unittest.TestCase):
    def saved_details(self, database):
        updates = [params for sql, params in database.cursor_.statements if sql.startswith("UPDATE companies SET listing_details")]
        return json.loads(updates[-1][0]) if updates else None

    def test_a_job_turned_applied_gets_todays_date(self):
        database = mark({"application_status": "None", "listing_details": json.dumps({"location": "Dallas, TX"})})
        details = self.saved_details(database)
        self.assertEqual(details["applied_on"], datetime.now().date().isoformat())
        self.assertEqual(details["location"], "Dallas, TX")
        self.assertEqual(database.commits, 1)

    def test_a_date_you_entered_is_kept(self):
        database = mark({"application_status": "Saved", "listing_details": json.dumps({"applied_on": "2026-09-30"})})
        self.assertIsNone(self.saved_details(database))

    def test_a_job_further_along_gets_no_date(self):
        database = mark({"application_status": "Interview", "listing_details": "{}"})
        self.assertIsNone(self.saved_details(database))

    def test_a_job_already_applied_before_dates_existed_is_left_alone(self):
        database = mark({"application_status": "Applied", "listing_details": "{}"})
        self.assertIsNone(self.saved_details(database))


if __name__ == "__main__":
    unittest.main()
