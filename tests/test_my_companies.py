import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import no_database  # noqa: F401  (cuts tests off from the real database; files go to a temporary user-data folder)
import dashboard
from jobfinder import paths
from jobfinder.web import companies_routes, my_companies

CENTER = (39.5359, -76.3483)  # made-up points: only the distances between them matter
NEAR = (39.5400, -76.3500)
FAR = (40.5, -76.3)


def reset():
    paths.MY_COMPANIES_FILE.unlink(missing_ok=True)


class Storage(unittest.TestCase):
    def setUp(self):
        reset()
        self.addCleanup(reset)

    def test_a_company_is_added_edited_and_deleted(self):
        with patch.object(my_companies, "place_point", return_value=NEAR):
            entry = my_companies.add({"name": "  Acme   Design ", "website": "acme.example.com", "address": "10 Main St",
                                      "place": "Bel Air, MD", "phone": "410-555-0100", "notes": "Met Sam at the fair"}, "MD")
        self.assertEqual(entry["name"], "Acme Design")
        self.assertEqual(entry["website"], "https://acme.example.com")
        self.assertEqual((entry["lat"], entry["lon"]), NEAR)
        self.assertEqual([c["name"] for c in my_companies.load()], ["Acme Design"])
        with patch.object(my_companies, "place_point", return_value=NEAR):
            changed = my_companies.update(entry["id"], {"name": "Acme Design Co", "place": "Bel Air, MD"}, "MD")
        self.assertEqual(changed["name"], "Acme Design Co")
        self.assertEqual(changed["added"], entry["added"])
        my_companies.delete(entry["id"])
        self.assertEqual(my_companies.load(), [])
        with self.assertRaises(KeyError):
            my_companies.delete(entry["id"])

    def test_a_name_is_required_and_a_bad_website_or_town_is_explained(self):
        with self.assertRaisesRegex(ValueError, "name"):
            my_companies.add({"name": "  "})
        with self.assertRaisesRegex(ValueError, "website"):
            my_companies.add({"name": "Acme", "website": "not a site"})
        with patch.object(my_companies, "place_point", return_value=None):
            with self.assertRaisesRegex(ValueError, "town"):
                my_companies.add({"name": "Acme", "place": "Nowhereville"})
        self.assertEqual(my_companies.load(), [])

    def test_a_zip_code_in_the_address_gives_the_map_point(self):
        with patch.object(my_companies, "zip_point", return_value=NEAR) as zip_point:
            entry = my_companies.add({"name": "Acme", "address": "10 Main St, Bel Air, MD 21014"})
        zip_point.assert_called_once_with("21014")
        self.assertEqual((entry["lat"], entry["lon"]), NEAR)

    def test_a_place_may_be_left_empty(self):
        entry = my_companies.add({"name": "Acme"})
        self.assertIsNone(entry["lat"])

    def test_the_file_is_the_users_own_json(self):
        my_companies.add({"name": "Acme"})
        data = json.loads(paths.MY_COMPANIES_FILE.read_text(encoding="utf-8"))
        self.assertEqual(data[0]["name"], "Acme")
        self.assertIn("user-data", str(paths.MY_COMPANIES_FILE))

    def test_unreadable_or_odd_files_are_an_empty_list(self):
        paths.MY_COMPANIES_FILE.parent.mkdir(parents=True, exist_ok=True)
        paths.MY_COMPANIES_FILE.write_text("not json", encoding="utf-8")
        self.assertEqual(my_companies.load(), [])
        paths.MY_COMPANIES_FILE.write_text(json.dumps({"a": 1}), encoding="utf-8")
        self.assertEqual(my_companies.load(), [])


class Nearby(unittest.TestCase):
    def setUp(self):
        reset()
        self.addCleanup(reset)
        with patch.object(my_companies, "place_point", side_effect=[NEAR, FAR, None]):
            my_companies.add({"name": "Near Co", "place": "Bel Air, MD"})
            my_companies.add({"name": "Far Co", "place": "Somewhere, PA"})
        my_companies.add({"name": "Anywhere Co"})  # no place: cannot be measured

    def test_near_ones_come_first_far_ones_are_left_out_and_unplaced_ones_are_kept(self):
        found = my_companies.near(*CENTER, 10)
        self.assertEqual([c["name"] for c in found], ["Near Co", "Anywhere Co"])
        self.assertIsNotNone(found[0]["miles"])
        self.assertIsNone(found[1]["miles"])
        self.assertTrue(all(c["mine"] and c["id"].startswith("mine/") for c in found))


class Routes(unittest.TestCase):
    def setUp(self):
        reset()
        self.addCleanup(reset)
        self.client = dashboard.app.test_client()
        with self.client.session_transaction() as session:
            session["csrf_token"] = "token"
        self.headers = {"X-CSRF-Token": "token"}
        patcher = patch.object(companies_routes.profile_store, "get_user_profile", return_value={"cities": [], "state": "MD"})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_add_edit_and_delete_over_the_page_routes(self):
        added = self.client.post("/companies/mine", json={"name": "Acme", "website": "acme.example.com"}, headers=self.headers)
        self.assertEqual(added.status_code, 200)
        company_id = added.get_json()["company"]["id"].split("/")[1]
        edited = self.client.post(f"/companies/mine/{company_id}", json={"name": "Acme Two"}, headers=self.headers)
        self.assertEqual(edited.get_json()["company"]["name"], "Acme Two")
        deleted = self.client.post(f"/companies/mine/{company_id}/delete", headers=self.headers)
        self.assertEqual(deleted.status_code, 200)
        again = self.client.post(f"/companies/mine/{company_id}/delete", headers=self.headers)
        self.assertEqual(again.status_code, 404)
        self.assertEqual(again.get_json()["error_code"], "E3512")

    def test_a_bad_entry_is_refused_with_a_message(self):
        response = self.client.post("/companies/mine", json={"name": ""}, headers=self.headers)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["error_code"], "E3511")

    def test_changes_need_the_page_token(self):
        self.assertEqual(self.client.post("/companies/mine", json={"name": "Acme"}).status_code, 403)

    def test_the_mine_kind_is_answered_from_the_users_file(self):
        my_companies.add({"name": "Acme"})
        with patch.object(companies_routes, "place_point", return_value=CENTER), \
                patch.object(companies_routes.local_companies, "search") as search:
            data = self.client.get("/companies/search?place=Bel+Air,+MD&kinds=mine").get_json()
        self.assertEqual([c["name"] for c in data["companies"]], ["Acme"])
        search.assert_not_called()

    def test_the_page_offers_the_form_and_the_kind(self):
        html = self.client.get("/companies").get_data(as_text=True)
        self.assertIn('id="companies-add-form"', html)
        self.assertIn('value="mine"', html)
        self.assertIn("My companies", html)


if __name__ == "__main__":
    unittest.main()
