import sys
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from mysql.connector import Error

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import no_database  # noqa: F401  (cuts tests off from the real database; files go to a temporary user-data folder)
import dashboard
from jobfinder import paths
from jobfinder.web import companies_routes, employer_sync, my_companies, profile_store

NEAR = (39.5400, -76.3500)  # made-up map point: only that there is one matters


def reset():
    paths.MY_COMPANIES_FILE.unlink(missing_ok=True)
    paths.MY_COMPANIES_REMOVED_FILE.unlink(missing_ok=True)


class ReadingAnAddress(unittest.TestCase):
    def split(self, address, place=""):
        return employer_sync.split_address(address, place)

    def test_the_way_the_companies_page_writes_an_address_is_read_back(self):
        self.assertEqual(self.split("757 Example Rd Suite 301, Springfield, MD, 21015"),
                         {"street": "757 Example Rd Suite 301", "city": "Springfield", "state": "MD", "zip": "21015"})
        self.assertEqual(self.split("10 Sample St # 250, Riverside, MD, 20855"),
                         {"street": "10 Sample St # 250", "city": "Riverside", "state": "MD", "zip": "20855"})

    def test_a_state_and_zip_together_or_a_state_name_are_read(self):
        self.assertEqual(self.split("100 Main St, Springfield, MD 21015"),
                         {"street": "100 Main St", "city": "Springfield", "state": "MD", "zip": "21015"})
        self.assertEqual(self.split("12 Oak Ave, Springfield, Maryland 21015"),
                         {"street": "12 Oak Ave", "city": "Springfield", "state": "MD", "zip": "21015"})

    def test_a_town_alone_a_zip_alone_and_nothing(self):
        self.assertEqual(self.split("Springfield, MD"), {"street": "", "city": "Springfield", "state": "MD", "zip": ""})
        self.assertEqual(self.split("21015"), {"street": "", "city": "", "state": "", "zip": "21015"})
        self.assertEqual(self.split(""), {"street": "", "city": "", "state": "", "zip": ""})

    def test_a_suite_on_its_own_is_not_taken_for_the_town(self):
        self.assertEqual(self.split("10 Main Street, Suite 4"),
                         {"street": "10 Main Street, Suite 4", "city": "", "state": "", "zip": ""})

    def test_what_the_address_lacks_comes_from_the_place_box(self):
        self.assertEqual(self.split("10 Main Street", "Springfield, MD"),
                         {"street": "10 Main Street", "city": "Springfield", "state": "MD", "zip": ""})
        self.assertEqual(self.split("Behind the mall", "21015")["zip"], "21015")
        self.assertEqual(self.split("1 A St, Town, MD, 21015", "Other, VA")["city"], "Town")  # the address wins

    def test_joined_back_it_reads_the_same(self):
        parts = {"street": "1 A St", "city": "Town", "state": "MD", "zip": "21015"}
        self.assertEqual(employer_sync.join_address(parts), "1 A St, Town, MD, 21015")
        self.assertEqual(self.split(employer_sync.join_address(parts)), parts)


class Pushing(unittest.TestCase):
    """What a company entry writes to the jobs in the work history."""

    def push(self, entry, before=None, result=1):
        with patch.object(profile_store, "update_work_details", return_value=result) as update:
            return employer_sync.push(entry, before), update

    ENTRY = {"name": "Example Co", "address": "10 Main Street, Springfield, MD, 21015", "place": "Springfield, MD",
             "phone": "410-555-0101", "website": "https://example.example.com"}

    def test_a_new_company_only_fills_what_the_job_has_no_text_for(self):
        count, update = self.push(self.ENTRY)
        self.assertEqual(count, 1)
        matches, values, fill_only = update.call_args.args
        self.assertTrue(fill_only)
        self.assertEqual(values, {"street": "10 Main Street", "city": "Springfield", "state": "MD", "zip": "21015",
                                  "phone": "410-555-0101", "website": "https://example.example.com"})
        self.assertTrue(matches("example co, inc."))  # the same company, written another way
        self.assertFalse(matches("Sample Corp"))

    def test_an_empty_box_on_a_new_company_writes_nothing(self):
        _, update = self.push({"name": "Example Co", "address": "", "place": "", "phone": "", "website": ""})
        update.assert_not_called()

    def test_an_edit_writes_only_the_boxes_that_changed(self):
        _, update = self.push(dict(self.ENTRY, phone="410-555-0199"), self.ENTRY)
        self.assertEqual(update.call_args.args[1], {"phone": "410-555-0199"})
        self.assertFalse(update.call_args.args[2])

    def test_a_phone_written_another_way_is_not_a_change(self):
        count, update = self.push(dict(self.ENTRY, phone="(410) 555-0101"), self.ENTRY)
        self.assertEqual(count, 0)
        update.assert_not_called()

    def test_a_website_with_www_and_a_slash_is_not_a_change(self):
        _, update = self.push(dict(self.ENTRY, website="https://www.example.example.com/"), self.ENTRY)
        update.assert_not_called()

    def test_a_changed_address_is_written_as_a_whole(self):
        _, update = self.push(dict(self.ENTRY, address="99 Other Road, Riverside, MD, 20855", place="Springfield, MD"), self.ENTRY)
        self.assertEqual(update.call_args.args[1], {"street": "99 Other Road", "city": "Riverside", "state": "MD", "zip": "20855"})

    def test_adding_a_website_to_a_company_that_had_none(self):
        before = dict(self.ENTRY, website="")
        _, update = self.push(self.ENTRY, before)
        self.assertEqual(update.call_args.args[1], {"website": "https://example.example.com"})

    def test_emptying_a_box_empties_it_on_the_job_too(self):
        _, update = self.push(dict(self.ENTRY, website=""), self.ENTRY)
        self.assertEqual(update.call_args.args[1], {"website": ""})

    def test_a_renamed_company_still_finds_its_job_by_the_old_name(self):
        _, update = self.push(dict(self.ENTRY, name="Example Company Renamed", phone="1"), self.ENTRY)
        self.assertTrue(update.call_args.args[0]("Example Co"))

    def test_a_profile_that_cannot_be_changed_is_reported(self):
        count, _ = self.push(dict(self.ENTRY, phone="1"), self.ENTRY, result=None)
        self.assertIsNone(count)


class FakeCursor:
    def __init__(self, jobs):
        self.jobs, self.updates = jobs, []

    def execute(self, sql, params=None):
        if sql.startswith("UPDATE"):
            self.updates.append((sql, params))

    def fetchall(self):
        return self.jobs


class WritingTheJobs(unittest.TestCase):
    JOBS = [{"id": 1, "company": "Example Co"}, {"id": 2, "company": "Sample Corp"}, {"id": 3, "company": "Example Co, Inc."}]

    def run_update(self, values, fill_only=False, jobs=None):
        cursor = FakeCursor(jobs or self.JOBS)

        @contextmanager
        def fake(**kwargs):
            yield cursor

        def same(company):
            return my_companies.same_company(company, "Example Co")

        with patch.object(profile_store, "ensure_profile_tables"), patch.object(profile_store.db, "cursor", fake):
            return profile_store.update_work_details(same, values, fill_only), cursor

    def test_every_job_at_the_company_is_changed_and_no_other(self):
        count, cursor = self.run_update({"phone": "410-555-0199", "website": "https://example.example.com"})
        self.assertEqual(count, 2)
        self.assertEqual([params[-1] for _, params in cursor.updates], [1, 3])
        self.assertEqual(cursor.updates[0][0], "UPDATE user_profile_work_history SET phone = %s, website = %s WHERE id = %s")
        self.assertEqual(cursor.updates[0][1], ("410-555-0199", "https://example.example.com", 1))

    def test_fill_only_leaves_a_detail_that_has_text_and_skips_empty_ones(self):
        count, cursor = self.run_update({"phone": "410-555-0199", "website": ""}, fill_only=True)
        self.assertEqual(count, 2)
        self.assertEqual(cursor.updates[0][0], "UPDATE user_profile_work_history SET phone = IF(phone = '', %s, phone) WHERE id = %s")

    def test_only_the_shared_details_can_be_changed(self):
        count, cursor = self.run_update({"supervisor_email": "boss@example.example.com", "role": "Boss"})
        self.assertEqual(count, 0)
        self.assertEqual(cursor.updates, [])

    def test_emptying_a_detail_writes_an_empty_text(self):
        _, cursor = self.run_update({"website": ""})
        self.assertEqual(cursor.updates[0][1], ("", 1))

    def test_a_database_that_fails_gives_none(self):
        def broken(**kwargs):
            raise Error("down")

        with patch.object(profile_store, "ensure_profile_tables"), patch.object(profile_store.db, "cursor", broken):
            self.assertIsNone(profile_store.update_work_details(lambda company: True, {"phone": "1"}))


class ShowingTheProfile(unittest.TestCase):
    def setUp(self):
        reset()
        self.addCleanup(reset)
        self.entry = my_companies.add({"name": "Example Co", "address": "10 Main Street, Springfield, MD, 21015",
                                       "phone": "410-555-0101", "website": "example.example.com", "notes": "mine"}, "MD")
        data = my_companies.load()
        data[0]["source"] = "resume"
        my_companies.save_all(data)

    def refresh(self, work_history):
        with patch.object(my_companies, "place_point", return_value=NEAR):
            return employer_sync.refresh_from_work_history(work_history, "MD"), my_companies.load()[0]

    def test_what_the_profile_has_shows_on_the_companies_page(self):
        changed, item = self.refresh([{"company": "example co", "street": "55 New Road", "city": "Riverside", "state": "MD",
                                       "zip": "20855", "phone": "410-555-0188", "website": "https://www.example-new.example.com/"}])
        self.assertEqual(changed, 1)
        self.assertEqual(item["address"], "55 New Road, Riverside, MD, 20855")
        self.assertEqual(item["phone"], "410-555-0188")
        self.assertEqual(item["website"], "https://www.example-new.example.com/")
        self.assertEqual(item["notes"], "mine")  # what is only on this page stays
        self.assertEqual(item["source"], "resume")

    def test_the_same_details_change_nothing(self):
        changed, _ = self.refresh([{"company": "Example Co", "street": "10 Main Street", "city": "Springfield", "state": "MD",
                                    "zip": "21015", "phone": "(410) 555-0101", "website": "https://www.example.example.com/"}])
        self.assertEqual(changed, 0)

    def test_a_profile_with_empty_boxes_never_empties_the_company(self):
        changed, item = self.refresh([{"company": "Example Co", "street": "", "city": "", "state": "", "zip": "", "phone": "", "website": ""}])
        self.assertEqual(changed, 0)
        self.assertEqual(item["phone"], "410-555-0101")

    def test_an_address_is_merged_part_by_part(self):
        changed, item = self.refresh([{"company": "Example Co", "city": "Riverside", "state": "MD"}])
        self.assertEqual(changed, 1)
        self.assertEqual(item["address"], "10 Main Street, Riverside, MD, 21015")

    def test_a_company_you_entered_yourself_is_left_alone(self):
        data = my_companies.load()
        data[0].pop("source")
        my_companies.save_all(data)
        changed, item = self.refresh([{"company": "Example Co", "phone": "999"}])
        self.assertEqual(changed, 0)
        self.assertEqual(item["phone"], "410-555-0101")


class Routes(unittest.TestCase):
    PROFILE = {"cities": [], "state": "MD", "work_history": []}

    def setUp(self):
        reset()
        self.addCleanup(reset)
        self.client = dashboard.app.test_client()
        with self.client.session_transaction() as session:
            session["csrf_token"] = "token"
        self.headers = {"X-CSRF-Token": "token"}
        patcher = patch.object(companies_routes.profile_store, "get_user_profile", return_value=self.PROFILE)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_adding_a_company_fills_its_job_and_says_how_many(self):
        with patch.object(profile_store, "update_work_details", return_value=2) as update:
            reply = self.client.post("/companies/mine", json={"name": "Example Co", "phone": "410-555-0101"}, headers=self.headers).get_json()
        self.assertEqual((reply["jobs_synced"], reply["profile_synced"]), (2, True))
        self.assertTrue(update.call_args.args[2])  # fill only

    def test_a_company_with_no_job_adds_fine(self):
        with patch.object(profile_store, "update_work_details", return_value=0):
            reply = self.client.post("/companies/mine", json={"name": "Elsewhere Inc"}, headers=self.headers).get_json()
        self.assertEqual(reply["jobs_synced"], 0)

    def test_an_edit_goes_to_the_job_and_to_the_list(self):
        company_id = my_companies.add({"name": "Example Co", "phone": "410-555-0101"})["id"]
        with patch.object(profile_store, "update_work_details", return_value=1) as update:
            reply = self.client.post(f"/companies/mine/{company_id}", json={"name": "Example Co", "phone": "410-555-0199"},
                                     headers=self.headers).get_json()
        self.assertEqual(reply["jobs_synced"], 1)
        self.assertEqual(update.call_args.args[1], {"phone": "410-555-0199"})
        self.assertEqual(my_companies.get(company_id)["phone"], "410-555-0199")

    def test_a_company_you_entered_yourself_is_saved_even_when_the_profile_cannot_be_changed(self):
        company_id = my_companies.add({"name": "Example Co", "phone": "410-555-0101"})["id"]
        with patch.object(profile_store, "update_work_details", return_value=None):
            reply = self.client.post(f"/companies/mine/{company_id}", json={"name": "Example Co", "phone": "410-555-0199"},
                                     headers=self.headers).get_json()
        self.assertEqual(reply["profile_synced"], False)
        self.assertEqual(my_companies.get(company_id)["phone"], "410-555-0199")

    def test_an_employer_from_your_resume_saves_nothing_when_the_profile_cannot_be_changed(self):
        company_id = my_companies.add({"name": "Example Co", "phone": "410-555-0101"})["id"]
        data = my_companies.load()
        data[0]["source"] = "resume"
        my_companies.save_all(data)
        with patch.object(profile_store, "update_work_details", return_value=None):
            response = self.client.post(f"/companies/mine/{company_id}", json={"name": "Example Co", "phone": "410-555-0199"},
                                        headers=self.headers)
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.get_json()["error_code"], "E3513")
        self.assertEqual(my_companies.get(company_id)["phone"], "410-555-0101")

    def test_a_bad_edit_is_still_refused_before_the_profile_is_touched(self):
        company_id = my_companies.add({"name": "Example Co"})["id"]
        with patch.object(profile_store, "update_work_details") as update:
            response = self.client.post(f"/companies/mine/{company_id}", json={"name": ""}, headers=self.headers)
        self.assertEqual(response.status_code, 400)
        update.assert_not_called()

    def test_opening_the_page_shows_what_the_profile_has(self):
        profile = dict(self.PROFILE, work_history=[{"company": "Example Co", "role": "Web Designer", "dates": "2025", "city": "Springfield",
                                                   "state": "MD", "street": "10 Main Street", "zip": "21015", "phone": "410-555-0101"}])
        with patch.object(companies_routes.profile_store, "get_user_profile", return_value=profile), \
                patch.object(my_companies, "place_point", return_value=NEAR):
            self.client.get("/companies")  # adds the employer from the work history
            profile["work_history"][0]["phone"] = "410-555-0166"  # then the Profile changes
            self.client.get("/companies")
        self.assertEqual(my_companies.load()[0]["phone"], "410-555-0166")


if __name__ == "__main__":
    unittest.main()
