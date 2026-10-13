import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import no_database  # noqa: F401  (cuts tests off from the real database)
from jobfinder.web import listing_queries


class TurnedDownTitles(unittest.TestCase):
    def test_only_a_thumbs_down_teaches_top_picks(self):
        cursor = MagicMock()
        cursor.fetchall.return_value = [("Forklift Operator",)]
        connection = MagicMock()
        connection.cursor.return_value = cursor
        with patch.object(listing_queries.db, "connect", return_value=connection):
            self.assertEqual(listing_queries.turned_down_titles(), ["Forklift Operator"])
        sql = cursor.execute.call_args[0][0]
        self.assertIn("pick_rating", sql)
        self.assertNotIn("is_rejected", sql)
        self.assertNotIn("rejection_reason", sql)

    def test_a_closed_rejection_is_an_allowed_reason(self):
        source = (Path(__file__).resolve().parents[1] / "jobfinder" / "web" / "listing_actions.py").read_text(encoding="utf-8")
        self.assertIn('"closed"', source)


if __name__ == "__main__":
    unittest.main()
