import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import no_database  # noqa: F401  (cuts tests off from the real database)
import dashboard
from jobfinder.sources import address_lookup as al
from jobfinder.web import companies_routes

FEATURES = [
    {"properties": {"name": "Example Studio", "housenumber": "757", "street": "Example Road", "city": "Springfield",
                    "state": "Maryland", "postcode": "21228", "countrycode": "US", "osm_key": "office", "osm_value": "company"}},
    {"properties": {"name": "Example Studio", "housenumber": "757", "street": "Example Road", "city": "Springfield",
                    "state": "Maryland", "postcode": "21228", "countrycode": "US", "osm_key": "building"}},  # same address
    {"properties": {"name": "Bus stop", "street": "Example Road", "city": "Springfield", "state": "Maryland", "countrycode": "US",
                    "osm_key": "highway"}},
    {"properties": {"name": "Somewhere", "housenumber": "1", "street": "Rue X", "city": "Paris", "countrycode": "FR"}},
    {"properties": {"name": "Springfield", "city": "Springfield", "state": "Maryland", "countrycode": "US"}},  # no street
]


def answer(features=FEATURES, status=200):
    response = MagicMock(status_code=status)
    response.json.return_value = {"type": "FeatureCollection", "features": features}
    return response


class Suggestions(unittest.TestCase):
    def setUp(self):
        al.clear_cache()

    def test_only_us_street_addresses_are_kept_once_each(self):
        found = al.format_features(FEATURES)
        self.assertEqual([item["address"] for item in found],
                         ["757 Example Road, Springfield, MD 21228", "Example Road, Springfield, MD"])
        self.assertEqual(found[0]["place"], "Springfield, MD")
        self.assertEqual(found[0]["name"], "Example Studio")
        self.assertEqual(found[1]["name"], "")

    def test_short_text_asks_nothing(self):
        get = MagicMock()
        self.assertEqual(al.suggest("75", http=get), [])
        get.assert_not_called()

    def test_the_question_is_biased_to_the_place_and_kept(self):
        get = MagicMock(return_value=answer())
        first = al.suggest("757 Example", (39.4612, -76.2701), http=get, sleep=lambda s: None)
        second = al.suggest("757 example", (39.4612, -76.2701), http=get, sleep=lambda s: None)
        self.assertEqual(first, second)
        self.assertEqual(get.call_count, 1)
        params = get.call_args.kwargs["params"]
        self.assertEqual((params["lat"], params["lon"]), (39.46, -76.27))
        self.assertIn("JobFinder", get.call_args.kwargs["headers"]["User-Agent"])

    def test_a_service_that_fails_is_a_message(self):
        with self.assertRaises(ValueError):
            al.suggest("757 Example", http=MagicMock(side_effect=requests.ConnectionError("x")), sleep=lambda s: None)
        al.clear_cache()
        with self.assertRaises(ValueError):
            al.suggest("757 Example", http=MagicMock(return_value=answer(status=503)), sleep=lambda s: None)


class Route(unittest.TestCase):
    def test_route_returns_suggestions_and_errors(self):
        client = dashboard.app.test_client()
        with patch.object(companies_routes.profile_store, "get_user_profile", return_value={"cities": [], "state": "MD"}), \
                patch.object(companies_routes, "resolve_place", return_value=((39.4, -76.3), "Bel Air, MD")), \
                patch.object(companies_routes.address_lookup, "suggest", return_value=[{"address": "1 A St"}]) as suggest:
            data = client.get("/companies/address-suggest?q=1+A+St&place=Bel+Air").get_json()
            self.assertEqual(data["suggestions"], [{"address": "1 A St"}])
            self.assertEqual(suggest.call_args.args[1], (39.4, -76.3))
            suggest.side_effect = ValueError("down")
            response = client.get("/companies/address-suggest?q=1+A+St")
            self.assertEqual((response.status_code, response.get_json()["error_code"]), (502, "E3521"))
            self.assertEqual(client.get("/companies/address-suggest?q=1").get_json()["suggestions"], [])

    def test_the_page_loads_the_script(self):
        html = dashboard.app.test_client().get("/companies").get_data(as_text=True)
        self.assertIn("address-suggest.js", html)



class FourBoxes(unittest.TestCase):
    def test_street_city_state_and_zip_are_kept_as_an_address_and_come_back_as_parts(self):
        from jobfinder.web import my_companies
        with patch.object(my_companies, "place_point", return_value=(39.5, -76.3)):
            values = my_companies.clean({"name": "Example Co", "street": "10 Main Street", "city": "Springfield",
                                         "state": "MD", "zip": "21015"})
        self.assertEqual(values["address"], "10 Main Street, Springfield, MD, 21015")
        self.assertEqual(values["place"], "Springfield, MD")
        shown = my_companies.as_result(dict(values, id="x"))
        self.assertEqual((shown["street"], shown["city"], shown["state"], shown["zip"]),
                         ("10 Main Street", "Springfield", "MD", "21015"))

    def test_a_zip_alone_is_the_place(self):
        from jobfinder.web import my_companies
        with patch.object(my_companies, "zip_point", return_value=(39.5, -76.3)):
            values = my_companies.clean({"name": "Example Co", "zip": "21015"})
        self.assertEqual((values["address"], values["place"]), ("21015", "21015"))

    def test_the_suggestion_has_the_four_parts(self):
        (first, *_) = al.format_features(FEATURES)
        self.assertEqual((first["street"], first["city"], first["state"], first["zip"]),
                         ("757 Example Road", "Springfield", "MD", "21228"))


if __name__ == "__main__":
    unittest.main()
