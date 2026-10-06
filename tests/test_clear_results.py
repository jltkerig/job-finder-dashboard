import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import no_database  # noqa: F401  (cuts tests off from the real database)
import dashboard
from jobfinder.web import search_control


class ClearResultsTests(unittest.TestCase):
    def setUp(self):
        self.client = dashboard.app.test_client()
        with self.client.session_transaction() as session:
            session["csrf_token"] = "t"

    def test_deletes_only_untouched_listings(self):
        cursor = MagicMock(rowcount=7)
        connection = MagicMock()
        connection.cursor.return_value = cursor
        with patch("jobfinder.web.clear_results.db.connect", return_value=connection), \
                patch.object(search_control, "scraper_process", None):
            response = self.client.post("/clear-results", headers={"X-CSRF-Token": "t"})
        self.assertEqual(response.get_json()["count"], 7)
        sql = cursor.execute.call_args[0][0]
        for kept in ("is_kept = 0", "is_rejected = 0", "application_status", "apply_queued"):
            self.assertIn(kept, sql)

    def test_refused_while_a_search_runs(self):
        running = MagicMock()
        running.poll.return_value = None
        with patch.object(search_control, "scraper_process", running), \
                patch("jobfinder.web.clear_results.db.connect") as connect:
            response = self.client.post("/clear-results", headers={"X-CSRF-Token": "t"})
        self.assertEqual(response.status_code, 409)
        connect.assert_not_called()


if __name__ == "__main__":
    unittest.main()
