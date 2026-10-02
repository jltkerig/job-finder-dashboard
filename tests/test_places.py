import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import no_database  # noqa: F401  (cuts tests off from the real database)
from places import city_matches


class CityTypeAhead(unittest.TestCase):
    def test_mid_sized_cities_are_there(self):
        self.assertIn("Lancaster, PA", city_matches("Lancaster"))
        self.assertIn("York, PA", city_matches("York, PA"))

    def test_near_home_comes_first(self):
        self.assertEqual(city_matches("Lancaster", "MD")[0], "Lancaster, PA")
        self.assertEqual(city_matches("Lancaster", "CA")[0], "Lancaster, CA")

    def test_unincorporated_places_and_state_filter(self):
        self.assertIn("Abingdon, MD", city_matches("Abingdon", "MD"))
        self.assertIn("Bel Air South, MD", city_matches("Bel Air", "MD"))
        self.assertTrue(all(m.endswith(", PA") for m in city_matches("Lan, PA")))

    def test_state_names_and_short_input(self):
        self.assertEqual(city_matches("Maryl")[0], "Maryland")
        self.assertEqual(city_matches("L"), [])


if __name__ == "__main__":
    unittest.main()
