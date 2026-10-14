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
    paths.MY_COMPANIES_REMOVED_FILE.unlink(missing_ok=True)


WORK_HISTORY = [
    {"company": "Example Co", "role": "Web Designer", "dates": "2025-01 – 2025-05", "city": "Springfield", "state": "MD",
     "zip": "21015", "street": "10 Main Street", "phone": "410-555-0101", "website": "example.example.com"},
    {"company": "Sample Corp", "role": "Front End Web Developer", "dates": "2015-05 – 2024-10"},
    {"company": "example co, inc.", "role": "Intern", "dates": "2013"},  # the same company again
    {"company": "", "role": "No company"},
]


class FromTheResume(unittest.TestCase):
    def setUp(self):
        reset()
        self.addCleanup(reset)

    def test_each_employer_is_added_once_with_what_the_work_history_knows(self):
        with patch.object(my_companies, "place_point", return_value=NEAR):
            added = my_companies.sync_from_work_history(WORK_HISTORY, "MD")
        self.assertEqual(added, 2)
        first, second = my_companies.load()
        self.assertEqual(first["name"], "Example Co")
        self.assertEqual(first["source"], "resume")
        self.assertEqual(first["website"], "https://example.example.com")
        self.assertEqual(first["address"], "10 Main Street, Springfield, MD, 21015")
        self.assertEqual(first["phone"], "410-555-0101")
        self.assertEqual((first["lat"], first["lon"]), NEAR)
        self.assertIn("Web Designer", first["notes"])
        self.assertEqual(second["name"], "Sample Corp")
        self.assertIsNone(second["lat"])  # no place known: still listed

    def test_running_it_again_adds_nothing(self):
        my_companies.sync_from_work_history(WORK_HISTORY, "MD")
        self.assertEqual(my_companies.sync_from_work_history(WORK_HISTORY, "MD"), 0)
        self.assertEqual(len(my_companies.load()), 2)

    def test_a_company_you_entered_yourself_is_not_added_again(self):
        my_companies.add({"name": "Sample Corporation"})
        self.assertEqual(my_companies.sync_from_work_history(WORK_HISTORY[1:2], "MD"), 0)

    def test_one_you_deleted_does_not_come_back_but_your_own_do_not_block_others(self):
        my_companies.sync_from_work_history(WORK_HISTORY, "MD")
        gone = next(item for item in my_companies.load() if item["name"] == "Sample Corp")
        my_companies.delete(gone["id"])
        self.assertEqual(my_companies.sync_from_work_history(WORK_HISTORY, "MD"), 0)
        self.assertEqual([c["name"] for c in my_companies.load()], ["Example Co"])

    def test_an_unreadable_place_does_not_stop_the_company_being_listed(self):
        job = {"company": "Odd Place Inc", "role": "Designer", "city": "Nowhereville", "state": "ZZ"}
        with patch.object(my_companies, "place_point", return_value=None):
            self.assertEqual(my_companies.sync_from_work_history([job], "MD"), 1)
        self.assertEqual(my_companies.load()[0]["name"], "Odd Place Inc")

    def test_the_page_adds_them_when_it_opens(self):
        client = dashboard.app.test_client()
        profile = {"cities": [], "state": "MD", "work_history": WORK_HISTORY[1:2]}
        with patch.object(companies_routes.profile_store, "get_user_profile", return_value=profile):
            client.get("/companies")
        self.assertEqual([c["name"] for c in my_companies.load()], ["Sample Corp"])


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

    def test_a_job_board_link_is_kept_and_named_when_it_is_a_known_system(self):
        entry = my_companies.add({"name": "Acme", "jobs_board": "jobs.lever.co/acme"})
        shown = my_companies.as_result(entry)
        self.assertEqual(shown["jobs_url"], "https://jobs.lever.co/acme")
        self.assertEqual(shown["jobs_system"], "Lever")
        own = my_companies.as_result(my_companies.add({"name": "Beta", "jobs_board": "https://beta.example.com/careers"}))
        self.assertEqual((own["jobs_url"], own["jobs_system"]), ("https://beta.example.com/careers", ""))

    def test_a_job_board_may_be_just_a_name_and_is_never_refused(self):
        for text in ("Indeed", "the company's own portal", "not a link"):
            shown = my_companies.as_result(my_companies.add({"name": "Acme " + text, "jobs_board": text}))
            self.assertEqual(shown["jobs_board"], text)
            self.assertEqual(shown["jobs_url"], "")  # shown as text, not as a link
        self.assertEqual(my_companies.as_result(my_companies.add({"name": "Plain"}))["jobs_board"], "")

    def test_the_older_field_name_is_still_read(self):
        entry = my_companies.add({"name": "Acme", "jobs_url": "jobs.lever.co/acme"})
        self.assertEqual(my_companies.as_result(entry)["jobs_url"], "https://jobs.lever.co/acme")

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

    def test_the_page_has_the_form_and_a_box_of_its_own_for_your_companies(self):
        html = self.client.get("/companies").get_data(as_text=True)
        self.assertIn('id="companies-add-form"', html)
        self.assertIn('id="companies-mine"', html)
        self.assertIn('id="companies-mine-body"', html)
        self.assertNotIn('value="mine"', html)  # not a kind to tick: they are always in their own box

    def test_the_box_lists_all_of_them_nearest_first_including_far_and_unplaced_ones(self):
        with patch.object(my_companies, "place_point", side_effect=[NEAR, FAR]):
            my_companies.add({"name": "Near Co", "place": "Bel Air, MD"})
            my_companies.add({"name": "Far Co", "place": "Somewhere, PA"})
        my_companies.add({"name": "Anywhere Co"})
        with patch.object(companies_routes, "place_point", return_value=CENTER):
            data = self.client.get("/companies/mine?place=Bel+Air,+MD").get_json()
        self.assertEqual([c["name"] for c in data["companies"]], ["Near Co", "Far Co", "Anywhere Co"])
        self.assertEqual(data["place"], "Bel Air, MD")
        self.assertGreater(data["companies"][1]["miles"], 50)  # far, but still listed
        self.assertIsNone(data["companies"][2]["miles"])

    def test_the_box_still_lists_them_when_the_place_is_unknown(self):
        my_companies.add({"name": "Acme"})
        with patch.object(companies_routes, "place_point", return_value=None):
            data = self.client.get("/companies/mine?place=Nowhereville").get_json()
        self.assertEqual([c["name"] for c in data["companies"]], ["Acme"])
        self.assertIsNone(data["companies"][0]["miles"])


if __name__ == "__main__":
    unittest.main()
