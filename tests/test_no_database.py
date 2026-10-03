from jobfinder.web import listing_queries
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import no_database  # noqa: F401  (cuts tests off from the real database)
import mysql.connector

import dashboard


class NoRealDatabase(unittest.TestCase):
    def test_connecting_fails(self):
        with self.assertRaises(mysql.connector.Error):
            mysql.connector.connect(host="127.0.0.1", user="root", database="job_finder")

    def test_app_code_falls_back_instead_of_reading_real_data(self):
        self.assertEqual(listing_queries.get_dashboard_counts()["saved"], 0)


if __name__ == "__main__":
    unittest.main()
