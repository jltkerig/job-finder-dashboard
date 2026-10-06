import json
import sys
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import no_database  # noqa: F401  (cuts tests off from the real database)
import dashboard
from jobfinder.web import auto_apply


class ScheduleTests(unittest.TestCase):
    def test_due_after_the_hour_once_a_day(self):
        on = {"daily_auto_search": True, "auto_search_hour": 8}
        self.assertFalse(auto_apply.search_due(on, datetime(2026, 10, 6, 7)))
        self.assertTrue(auto_apply.search_due(on, datetime(2026, 10, 6, 9)))
        self.assertFalse(auto_apply.search_due(dict(on, auto_search_last_date="2026-10-06"), datetime(2026, 10, 6, 9)))
        self.assertFalse(auto_apply.search_due({}, datetime(2026, 10, 6, 9)))

    def test_queue_pending_until_the_picks_are_queued(self):
        self.assertFalse(auto_apply.auto_queue_pending({}))
        self.assertTrue(auto_apply.auto_queue_pending({"auto_search_last_date": "2026-10-06"}))
        self.assertFalse(auto_apply.auto_queue_pending({"auto_search_last_date": "2026-10-06", "auto_queue_date": "2026-10-06"}))

    def test_busy_search_is_retried_not_marked_done(self):
        with patch.object(auto_apply, "read_tuning_settings", return_value={"daily_auto_search": True, "auto_search_hour": 0}), \
                patch.object(auto_apply, "run_daily_search", return_value=False), \
                patch.object(auto_apply, "write_settings") as write:
            auto_apply._tick()
        write.assert_not_called()


class QueueRouteTests(unittest.TestCase):
    def setUp(self):
        self.client = dashboard.app.test_client()
        with self.client.session_transaction() as session:
            session["csrf_token"] = "t"

    def test_queue_marks_listing_and_keeps_it(self):
        cursor = MagicMock()
        cursor.fetchall.return_value = [{"id": 5, "listing_details": '{"pick_rating": "up"}'}]
        connection = MagicMock()
        connection.cursor.return_value = cursor
        with patch("jobfinder.web.auto_apply.db.connect", return_value=connection), \
                patch("jobfinder.web.auto_apply.ensure_keep_column"), \
                patch("jobfinder.web.auto_apply.ensure_job_tracking_columns"):
            response = self.client.post("/apply-queue", json={"company_ids": [5]}, headers={"X-CSRF-Token": "t"})
        self.assertEqual(response.status_code, 200)
        saved = json.loads(cursor.execute.call_args_list[1][0][1][0])
        self.assertEqual(saved, {"pick_rating": "up", "apply_queued": True})
        self.assertIn("is_kept = 1", cursor.execute.call_args_list[2][0][0])

    def test_bad_ids_are_refused(self):
        with patch("jobfinder.web.auto_apply.ensure_keep_column"), patch("jobfinder.web.auto_apply.ensure_job_tracking_columns"):
            response = self.client.post("/apply-queue", json={"company_ids": ["x"]}, headers={"X-CSRF-Token": "t"})
        self.assertEqual(response.status_code, 400)

    def test_queue_lists_only_unapplied(self):
        cursor = MagicMock()
        cursor.fetchall.return_value = [
            {"id": 1, "career_url": "https://a", "source_url": "", "application_status": "Saved", "listing_details": '{"apply_queued": true}'},
            {"id": 2, "career_url": "https://b", "source_url": "", "application_status": "Applied", "listing_details": '{"apply_queued": true}'},
            {"id": 3, "career_url": "https://c", "source_url": "", "application_status": "Saved", "listing_details": '{"x": "apply_queued"}'},
        ]
        connection = MagicMock()
        connection.cursor.return_value = cursor
        with patch("jobfinder.web.auto_apply.db.connect", return_value=connection):
            self.assertEqual([job["id"] for job in auto_apply.get_apply_queue()], [1])


if __name__ == "__main__":
    unittest.main()
