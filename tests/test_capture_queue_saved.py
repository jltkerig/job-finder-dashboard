import json
import sys
import unittest
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


def queue(row):
    run = object.__new__(CaptureImport)
    run.database = FakeDatabase(row)
    run._queue_saved(7)
    return run.database


def written(database):
    updates = [params for sql, params in database.cursor_.statements if sql.startswith("UPDATE companies")]
    return json.loads(updates[-1][0]) if updates else None


class QueueSavedJob(unittest.TestCase):
    def test_a_saved_job_is_kept_and_put_on_the_queue(self):
        database = queue({"application_status": "None", "is_rejected": 0, "listing_details": json.dumps({"location": "Dallas, TX"})})
        details = written(database)
        self.assertTrue(details["apply_queued"])
        self.assertRegex(details["apply_queued_at"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$")
        self.assertEqual(details["location"], "Dallas, TX")
        sql = database.cursor_.statements[-1][0]
        self.assertIn("is_kept = 1", sql)
        self.assertIn("THEN 'Saved'", sql)
        self.assertEqual(database.commits, 1)

    def test_a_job_already_queued_keeps_its_date(self):
        database = queue({"application_status": "Saved", "is_rejected": 0,
                          "listing_details": json.dumps({"apply_queued": True, "apply_queued_at": "2026-10-01T09:00:00"})})
        self.assertEqual(written(database)["apply_queued_at"], "2026-10-01T09:00:00")

    def test_applied_closed_and_rejected_jobs_are_left_alone(self):
        for status, rejected in (("Applied", 0), ("Interview", 0), ("Closed", 0), ("Rejected", 0), ("None", 1)):
            database = queue({"application_status": status, "is_rejected": rejected, "listing_details": "{}"})
            self.assertIsNone(written(database), (status, rejected))
            self.assertEqual(database.commits, 0)


if __name__ == "__main__":
    unittest.main()
