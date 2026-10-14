import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import no_database  # noqa: F401  (cuts tests off from the real database)
import dashboard
from jobfinder.sources import local_companies as lc
from jobfinder.web import companies_routes

CENTER = (39.5359, -76.3483)  # made-up places: only the distances between points matter

ITEMS = [
    {"osm_type": "node", "osm_id": 1, "lat": "39.5400", "lon": "-76.3500", "category": "office", "type": "insurance",
     "name": "Acme Insurance", "address": {"house_number": "10", "road": "Main Street", "town": "Springfield",
                                           "state": "Maryland", "ISO3166-2-lvl4": "US-MD", "postcode": "21015"},
     "extratags": {"website": "acme.example.com", "phone": "+1 410 555 0100"}},
    {"osm_type": "way", "osm_id": 2, "lat": "39.6000", "lon": "-76.3000", "category": "craft", "type": "metal_construction",
     "name": "Riverside Machining", "address": {"road": "Industrial Way", "city": "Riverside", "state": "Maryland"},
     "extratags": {"contact:website": "https://riverside.example.com/"}},
    {"osm_type": "node", "osm_id": 3, "lat": "39.5401", "lon": "-76.3501", "category": "office", "type": "insurance",
     "name": "acme insurance", "address": {"house_number": "10", "road": "Main Street", "town": "Springfield",
                                            "state": "Maryland", "ISO3166-2-lvl4": "US-MD", "postcode": "21015"}},  # twice
    {"osm_type": "node", "osm_id": 4, "lat": "39.54", "lon": "-76.35", "category": "office", "type": "yes", "name": ""},
    {"osm_type": "node", "osm_id": 5, "lat": "x", "lon": "y", "name": "No Place"},
    {"osm_type": "node", "osm_id": 6, "lat": "41.5", "lon": "-76.3", "category": "shop", "type": "supermarket",
     "name": "Far Away Market", "address": {}},
]


def answer(items=None, status=200):
    response = MagicMock(status_code=status)
    response.json.return_value = ITEMS if items is None else items
    return response


def no_wait(seconds):
    pass


class Parsing(unittest.TestCase):
    def test_companies_come_out_nearest_first_with_their_details(self):
        found = lc.parse_places(ITEMS, CENTER, 50)
        self.assertEqual([c["name"] for c in found], ["Acme Insurance", "Riverside Machining"])
        first = found[0]
        self.assertEqual(first["type"], "Insurance")
        self.assertEqual(first["address"], "10 Main Street, Springfield, MD 21015")
        self.assertEqual(first["website"], "https://acme.example.com")
        self.assertEqual(first["phone"], "+1 410 555 0100")
        self.assertEqual(first["id"], "node/1")
        self.assertLess(first["miles"], found[1]["miles"])
        self.assertEqual(found[1]["type"], "Metal construction")
        self.assertEqual(found[1]["website"], "https://riverside.example.com/")
        self.assertEqual(found[1]["address"], "Industrial Way, Riverside, Maryland")

    def test_places_in_the_corners_beyond_the_distance_are_left_out(self):
        near = lc.parse_places(ITEMS, CENTER, 2)
        self.assertEqual([c["name"] for c in near], ["Acme Insurance"])

    def test_distance_is_straight_line_miles(self):
        self.assertAlmostEqual(lc.miles_between((39.0, -76.0), (40.0, -76.0)), 69.1, delta=0.3)

    def test_the_search_box_holds_the_circle(self):
        left, top, right, bottom = (float(part) for part in lc.viewbox(39.5, -76.3, 10).split(","))
        self.assertLess(left, -76.3)
        self.assertGreater(right, -76.3)
        self.assertAlmostEqual(top - 39.5, 10 / 69.0, places=2)
        self.assertGreater(top, bottom)


class Searching(unittest.TestCase):
    def setUp(self):
        lc.clear_cache()

    def test_each_word_of_a_kind_is_asked_and_the_answers_are_merged(self):
        get = MagicMock(side_effect=lambda url, **kwargs: answer(ITEMS if kwargs["params"]["q"] == "company" else []))
        found = lc.search(*CENTER, 10, ["offices"], http=get, sleep=no_wait)
        asked = [call.kwargs["params"]["q"] for call in get.call_args_list]
        self.assertEqual(asked, lc.KINDS["offices"][1])
        self.assertEqual([c["name"] for c in found], ["Acme Insurance", "Riverside Machining"])
        params = get.call_args_list[0].kwargs["params"]
        self.assertEqual((params["bounded"], params["limit"]), (1, 40))
        self.assertIn("JobFinder", get.call_args_list[0].kwargs["headers"]["User-Agent"])

    def test_requests_are_a_second_apart(self):
        waits = []
        get = MagicMock(return_value=answer([]))
        lc.search(*CENTER, 10, ["finance", "education"], http=get, sleep=waits.append)
        self.assertEqual(get.call_count, len(lc.KINDS["finance"][1]) + len(lc.KINDS["education"][1]))
        self.assertTrue(all(0 < wait <= lc.GAP_SECONDS for wait in waits))

    def test_the_same_search_is_asked_once(self):
        get = MagicMock(return_value=answer())
        first = lc.search(*CENTER, 10, ["finance"], http=get, sleep=no_wait)
        calls = get.call_count
        second = lc.search(*CENTER, 10, ["finance"], http=get, sleep=no_wait)
        self.assertEqual(first, second)
        self.assertEqual(get.call_count, calls)

    def test_a_busy_service_is_waited_for_once_per_word(self):
        waits = []
        get = MagicMock(side_effect=[answer(status=429), answer()])
        found = lc.search(*CENTER, 10, ["finance"], http=get, sleep=waits.append)
        self.assertEqual(len(found), 2)
        self.assertIn(lc.RETRY_WAIT_SECONDS, waits)

    def test_a_word_that_fails_does_not_lose_the_others(self):
        words = lc.KINDS["healthcare"][1]

        def ask(url, **kwargs):
            word = kwargs["params"]["q"]
            if word == words[0]:
                raise requests.ConnectionError("down")  # the first word fails ...
            return answer() if word == words[1] else answer([])  # ... the second one has places, the rest none

        found = lc.search(*CENTER, 10, ["healthcare"], http=ask, sleep=no_wait)
        self.assertEqual(len(found), 2)

    def test_nothing_answering_is_a_message_for_the_page(self):
        get = MagicMock(side_effect=requests.ConnectionError("down"))
        with self.assertRaises(ValueError) as caught:
            lc.search(*CENTER, 10, ["finance"], http=get, sleep=no_wait)
        self.assertIn("Try again", str(caught.exception))


class Page(unittest.TestCase):
    def setUp(self):
        lc.clear_cache()
        self.client = dashboard.app.test_client()

    def test_the_page_has_the_search_form_and_the_nav_link(self):
        html = self.client.get("/companies").get_data(as_text=True)
        self.assertIn('id="companies-form"', html)
        self.assertIn('href="/companies">Companies</a>', html)
        self.assertIn('data-page="companies"', html)
        self.assertIn('value="known"', html)  # employers from your own searches come first
        self.assertIn("Employers from your searches", html)

    def test_a_search_resolves_the_place_and_returns_companies(self):
        profile = {"cities": [{"city": "Bel Air, MD", "radius": 20}], "home_zip": "21015", "state": "MD"}
        with patch.object(companies_routes.profile_store, "get_user_profile", return_value=profile), \
                patch.object(companies_routes, "place_point", return_value=CENTER), \
                patch.object(companies_routes.local_companies, "search", return_value=lc.parse_places(ITEMS, CENTER, 50)) as search:
            data = self.client.get("/companies/search?place=Bel+Air,+MD&miles=10&kinds=offices,healthcare").get_json()
        self.assertEqual(data["status"], "ok")
        self.assertEqual(data["count"], 2)
        self.assertEqual(data["place"], "Bel Air, MD")
        args = search.call_args.args
        self.assertEqual(args[2], 10.0)
        self.assertEqual(list(args[3]), ["offices", "healthcare"])

    def test_the_known_kind_comes_from_the_database_not_the_network(self):
        known = [{"id": "known/1", "name": "Acme", "type": "Hiring: Web Designer", "address": "Joppa, MD", "website": "",
                  "phone": "", "lat": 39.5, "lon": -76.3, "miles": 1.0}]
        with patch.object(companies_routes.profile_store, "get_user_profile", return_value={"cities": [], "state": "MD"}), \
                patch.object(companies_routes, "place_point", return_value=CENTER), \
                patch.object(companies_routes.listing_queries, "get_known_employers", return_value=known) as known_query, \
                patch.object(companies_routes.local_companies, "search") as search:
            data = self.client.get("/companies/search?place=Bel+Air,+MD&kinds=known").get_json()
        self.assertEqual(data["count"], 1)
        known_query.assert_called_once()
        search.assert_not_called()

    def test_an_unknown_place_and_a_blank_one_say_what_to_type(self):
        with patch.object(companies_routes.profile_store, "get_user_profile", return_value={"cities": [], "state": ""}), \
                patch.object(companies_routes, "place_point", return_value=None):
            unknown = self.client.get("/companies/search?place=Nowhereville")
            blank = self.client.get("/companies/search?place=")
        self.assertEqual(unknown.status_code, 404)
        self.assertEqual(unknown.get_json()["error_code"], "E3502")
        self.assertEqual(blank.status_code, 400)
        self.assertEqual(blank.get_json()["error_code"], "E3501")

    def test_a_zip_code_is_looked_up_as_a_zip(self):
        profile = {"cities": [], "state": "MD"}
        with patch.object(companies_routes.profile_store, "get_user_profile", return_value=profile), \
                patch.object(companies_routes, "zip_point", return_value=CENTER) as zip_point, \
                patch.object(companies_routes.local_companies, "search", return_value=[]):
            data = self.client.get("/companies/search?place=21015").get_json()
        zip_point.assert_called_once_with("21015")
        self.assertEqual(data["count"], 0)

    def test_the_service_being_down_is_a_clear_error(self):
        with patch.object(companies_routes.profile_store, "get_user_profile", return_value={"cities": [], "state": "MD"}), \
                patch.object(companies_routes, "place_point", return_value=CENTER), \
                patch.object(companies_routes.local_companies, "search", side_effect=ValueError("busy. Try again in a minute.")):
            response = self.client.get("/companies/search?place=Bel+Air,+MD&kinds=offices")
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.get_json()["error_code"], "E3503")


if __name__ == "__main__":
    unittest.main()
