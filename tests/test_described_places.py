import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import no_database  # noqa: F401  (cuts tests off from the real database)
from jobfinder.search.usa_location import arrangement_from_description, location_from_description


class PlacesAndArrangements(unittest.TestCase):
    def test_a_place_next_to_wording_like_role_is_full_time_in(self):
        self.assertEqual(location_from_description("Role is full time in Irving-Las Colinas, TX. All employees ..."), "Irving-Las Colinas, TX")
        self.assertEqual(location_from_description("Location: Austin, TX"), "Austin, TX")
        self.assertEqual(location_from_description("Based in Denver, CO. Remote ok"), "Denver, CO")

    def test_a_named_metro_area_gives_its_main_city(self):
        self.assertEqual(location_from_description("Serving the Dallas-Fort Worth Metroplex"), "Dallas, TX")
        self.assertEqual(location_from_description("Our DMV area clients"), "Washington, DC")

    def test_a_name_followed_by_a_state_code_is_not_a_place(self):
        self.assertEqual(location_from_description("Jane Doe, MD is our doctor"), "")
        self.assertEqual(location_from_description("Apply in person at 5 Main St, Towson, MD"), "")
        self.assertEqual(location_from_description(""), "")

    def test_days_in_the_office_make_a_job_hybrid(self):
        self.assertEqual(arrangement_from_description("All employees must work in our office 4 days a week, Wednesday from home"), "Hybrid")
        self.assertEqual(arrangement_from_description("Two days per week in the office"), "Hybrid")
        self.assertEqual(arrangement_from_description("This is a hybrid role"), "Hybrid")

    def test_plain_remote_and_onsite_wording(self):
        self.assertEqual(arrangement_from_description("This is a fully remote position"), "Remote")
        self.assertEqual(arrangement_from_description("On-site in Reston, VA"), "Onsite")
        self.assertEqual(arrangement_from_description("Role is full time in Towson, MD"), "Onsite")
        self.assertIsNone(arrangement_from_description("On-site or fully remote, you choose"))
        self.assertIsNone(arrangement_from_description("An on-site interview is held at our office"))
        self.assertIsNone(arrangement_from_description("Great benefits"))


if __name__ == "__main__":
    unittest.main()
