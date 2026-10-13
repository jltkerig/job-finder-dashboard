import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from jobfinder.records import job_retention


class OldUnsavedJobs(unittest.TestCase):
    def test_unsaved_results_are_deleted_after_14_days_here_but_never_saved_queued_or_rejected_ones(self):
        cursor = MagicMock()
        cursor.rowcount = 3
        self.assertEqual(job_retention.delete_old_unsaved(cursor), 3)
        sql, params = cursor.execute.call_args[0]
        self.assertIn("date_found < CURRENT_TIMESTAMP - INTERVAL %s DAY", sql)  # when it was first saved here
        self.assertIn("is_kept = 0", sql)
        self.assertIn("is_rejected = 0", sql)  # rejected rows remember what you turned down
        self.assertIn("apply_queued", sql)
        self.assertIn("NOT IN", sql)
        self.assertEqual(params[-1], 14)
        self.assertEqual(set(params[:-1]), set(job_retention.SAVED_STATUSES))
        self.assertEqual(job_retention.UNSAVED_KEEP_DAYS, 14)

    def test_the_posting_date_is_not_used(self):
        cursor = MagicMock()
        job_retention.delete_old_unsaved(cursor)
        self.assertNotIn("posted", cursor.execute.call_args[0][0])


if __name__ == "__main__":
    unittest.main()
