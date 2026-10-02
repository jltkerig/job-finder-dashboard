import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import no_database  # noqa: F401  (cuts tests off from the real database)
from owners import search_source
import dashboard
import job_finder
from jobfinder.profiles.profile_tools import fit_score, refresh_listing_skills

ROOT = Path(__file__).resolve().parents[1]


class FakeConnection:
    """Rows of (id, listing_skills, listing_details); remembers the updates it is given."""

    def __init__(self, rows):
        self.rows, self.updates, self.commits = rows, [], 0
        connection = self

        class Cursor:
            def execute(self, sql, params=None):
                if sql.lstrip().upper().startswith("UPDATE"):
                    connection.updates.append(params)

            def fetchall(self):
                return list(connection.rows)

            def close(self):
                pass

        self._cursor = Cursor()

    def cursor(self, *args, **kwargs):
        return self._cursor

    def commit(self):
        self.commits += 1

    def is_connected(self):
        return True

    def close(self):
        pass


def details(description):
    return json.dumps({"description": description})


class RereadingSavedListings(unittest.TestCase):
    def test_newly_recognized_skills_are_added_and_old_ones_kept(self):
        connection = FakeConnection([
            (1, json.dumps(["HTML"]), details("Build landing pages and run A/B testing with Figma.")),
        ])
        self.assertEqual(refresh_listing_skills(connection), 1)
        [(value, row_id)] = connection.updates
        self.assertEqual(row_id, 1)
        stored = json.loads(value)
        self.assertEqual(stored[0], "HTML")  # what was already there stays first
        for skill in ("Landing Pages", "A/B Testing", "Figma"):
            self.assertIn(skill, stored)
        self.assertEqual(connection.commits, 1)

    def test_a_skill_found_on_the_original_page_is_not_lost_when_the_saved_description_is_shorter(self):
        connection = FakeConnection([(2, json.dumps(["Drupal"]), details("Designs web pages."))])
        refresh_listing_skills(connection)
        stored = json.loads(connection.updates[0][0]) if connection.updates else ["Drupal"]
        self.assertIn("Drupal", stored)

    def test_listings_with_nothing_new_or_no_description_are_left_alone(self):
        connection = FakeConnection([
            (3, json.dumps(["Figma"]), details("Uses Figma every day.")),     # nothing new
            (4, json.dumps(["Figma"]), json.dumps({"posted": "2026-10-01"})),  # no description stored
            (5, "not json", details("Uses Photoshop.")),                       # unreadable stored skills
            (6, None, "not json either"),
        ])
        self.assertEqual(refresh_listing_skills(connection), 0)
        self.assertEqual(connection.updates, [])

    def test_a_listing_with_no_skills_stored_yet_gets_them(self):
        connection = FakeConnection([(7, None, details("Experience with WordPress and SEO."))])
        self.assertEqual(refresh_listing_skills(connection), 1)
        self.assertEqual(sorted(json.loads(connection.updates[0][0])), ["SEO", "WordPress"])

    def test_job_fit_follows_the_new_skills(self):
        listing = ["Figma", "SEO", "Landing Pages", "A/B Testing"]
        self.assertEqual(fit_score(["Figma"], listing)["score"], 25)
        self.assertEqual(fit_score(["Figma", "SEO", "Landing Pages"], listing)["score"], 75)


class WhenTheSkillsChange(unittest.TestCase):
    def test_refresh_job_fit_reads_the_saved_listings(self):
        connection = FakeConnection([(1, json.dumps([]), details("Experience with WordPress."))])
        with patch.object(dashboard.mysql.connector, "connect", return_value=connection):
            self.assertEqual(dashboard.refresh_job_fit(), 1)
        self.assertEqual(json.loads(connection.updates[0][0]), ["WordPress"])

    def test_with_no_database_the_refresh_just_returns_zero(self):
        self.assertEqual(dashboard.refresh_job_fit(), 0)  # the tests have no database

    def test_saving_changed_skills_goes_back_to_the_dashboard_with_the_result(self):
        source = (ROOT / "dashboard.py").read_text(encoding="utf-8")
        self.assertIn('redirect(f"/dashboard?fit_updated={refresh_job_fit()}")', source)
        self.assertIn('previous_skills = {str(skill).casefold() for skill in get_user_profile().get("skills", [])}', source)
        html = (ROOT / "templates" / "user-dashboard.html").read_text(encoding="utf-8")
        self.assertIn("{% if fit_updated is not none %}", html)
        self.assertIn("Job Fit is up to date", html)

    def test_every_search_and_refresh_starts_with_a_quiet_re_read(self):
        source = search_source()
        self.assertEqual(source.count("    refresh_job_fit_quietly(database)\n"), 2)

    def test_the_quiet_re_read_never_stops_a_run(self):
        job_finder.refresh_job_fit_quietly(object())  # no cursor at all: ignored


if __name__ == "__main__":
    unittest.main()
