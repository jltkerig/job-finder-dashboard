from jobfinder.web import profile_store
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import no_database  # noqa: F401  (cuts tests off from the real database)
import dashboard
from jobfinder.profiles import travel


class DistanceEstimate(unittest.TestCase):
    def test_zip_and_places_are_found(self):
        self.assertAlmostEqual(travel.zip_point("21009")[0], 39.47, places=1)
        self.assertIsNotNone(travel.place_point("Bel Air, MD"))
        self.assertEqual(travel.place_point("Baltimore, Maryland, United States"), travel.place_point("Baltimore, MD"))
        self.assertEqual(travel.place_point("Greater Baltimore Area", "MD"), travel.place_point("Baltimore, MD"))
        self.assertIsNone(travel.place_point("Nowhereville, ZZ"))

    def test_a_state_less_name_prefers_places_near_home(self):
        near, far = travel.place_point("Lancaster", "MD"), travel.place_point("Lancaster", "CA")
        self.assertGreater(near[1], far[1])  # Pennsylvania is east of California

    def test_nearby_is_quicker_than_far(self):
        bel_air = travel.describe("21009", job_text="Bel Air, MD")
        baltimore = travel.describe("21009", job_text="Baltimore, MD")
        self.assertLess(bel_air["miles"], baltimore["miles"])
        self.assertLess(bel_air["minutes"], baltimore["minutes"])
        self.assertTrue(baltimore["text"].startswith(f"{baltimore['miles']} mi · ~"))

    def test_trips_are_rounded_and_long_ones_in_hours(self):
        self.assertEqual(travel.describe("21009", job_text="Baltimore, MD")["minutes"] % 5, 0)
        self.assertIn("hr", travel.describe("21009", job_text="Austin, TX")["text"])

    def test_unknown_home_or_place_gives_none(self):
        self.assertIsNone(travel.describe("", job_text="Baltimore, MD"))
        self.assertIsNone(travel.describe("00000", job_text="Baltimore, MD"))
        self.assertIsNone(travel.describe("21009", job_text="Nowhereville, ZZ"))

    def test_saved_job_cards_get_the_estimate(self):
        jobs = [{"city": "Bel Air", "state": "MD", "latitude": None, "longitude": None},
                {"city": "Nowhereville", "state": "ZZ"}]
        dashboard.add_drive_times(jobs, "21009", "MD")
        self.assertTrue(jobs[0]["drive"]["text"].startswith(f"{jobs[0]['drive']['miles']} mi"))
        self.assertIsNone(jobs[1]["drive"])
        dashboard.add_drive_times(jobs, "", "MD")
        self.assertIsNone(jobs[0]["drive"])

    def test_the_extension_address_estimates_each_place(self):
        with patch.object(profile_store, "get_user_profile", return_value={"home_zip": "21009", "state": "MD"}):
            data = dashboard.app.test_client().get("/extension/distances?places=Bel%20Air,%20MD|Nowhereville,%20ZZ").get_json()
        self.assertEqual(data["home_zip"], "21009")
        self.assertIn("mi", data["places"]["Bel Air, MD"]["text"])
        self.assertIsNone(data["places"]["Nowhereville, ZZ"])
        with patch.object(profile_store, "get_user_profile", return_value={"home_zip": "", "state": ""}):
            data = dashboard.app.test_client().get("/extension/distances?places=Bel%20Air,%20MD").get_json()
        self.assertIsNone(data["places"]["Bel Air, MD"])


if __name__ == "__main__":
    unittest.main()
