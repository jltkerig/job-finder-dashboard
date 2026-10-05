import json
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import no_database  # noqa: F401  (cuts tests off from the real database)
from jobfinder.profiles.requirements import listing_requirements, requirement_gaps
import dashboard

PROFILE = {"education": [], "skills": ["Figma"], "job_titles": ["Graphic Designer"],
           "work_history": [{"role": "Graphic Designer", "company": "Acme", "dates": "2012 – Present",
                             "description": "Brand identity and web design."}]}


def gaps(text, profile=PROFILE):
    return requirement_gaps(listing_requirements(text), profile)


class RequirementTests(unittest.TestCase):
    def test_degree_without_alternative_is_hard(self):
        self.assertEqual(gaps("Bachelor's degree in Fashion Design required."),
                         [{"text": "Asks for a degree in Fashion Design", "hard": True}])

    def test_degree_or_years_counts_work_history(self):
        self.assertEqual(gaps("Bachelor's degree in Computer Science or 4 years of experience."), [])

    def test_degree_in_a_field_the_profile_shows_is_met(self):
        self.assertEqual(gaps("Bachelor's degree in Graphic Design required."), [])

    def test_fashion_school(self):
        self.assertTrue(gaps("Four years of fashion school required.")[0]["hard"])

    def test_preferred_lines_are_skipped(self):
        self.assertEqual(gaps("Master's degree in Fashion preferred. Top Secret clearance is a plus."), [])

    def test_active_clearance_is_hard(self):
        self.assertEqual(gaps("Active TS/SCI clearance required."),
                         [{"text": "Requires an active TS/SCI clearance", "hard": True}])

    def test_obtainable_clearance_is_soft(self):
        self.assertEqual(gaps("Must be able to obtain a Secret clearance."),
                         [{"text": "Must be able to get a Secret clearance", "hard": False}])

    def test_clearance_in_profile_is_met(self):
        profile = dict(PROFILE, skills=["Figma", "Active Secret clearance"])
        self.assertEqual(gaps("Active Secret clearance required.", profile), [])


class RouteTests(unittest.TestCase):
    def test_reads_page_once_and_keeps_requirements(self):
        cursor = MagicMock()
        cursor.fetchall.return_value = [{"id": 7, "career_url": "https://example.com/job", "source_url": "",
                                         "listing_details": "{}"}]
        connection = MagicMock()
        connection.cursor.return_value = cursor
        page = MagicMock(text="<p>Active Top Secret clearance required.</p>")
        client = dashboard.app.test_client()
        with client.session_transaction() as session:
            session["csrf_token"] = "t"
        with patch("jobfinder.web.listing_actions.db.connect", return_value=connection), \
                patch("jobfinder.search.relevance.fetch_text", return_value=page) as fetch, \
                patch("jobfinder.web.profile_store.get_user_profile", return_value=PROFILE):
            response = client.post("/top-picks/requirements", json={"company_ids": [7]}, headers={"X-CSRF-Token": "t"})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["gaps"]["7"]["items"][0]["hard"])
        fetch.assert_called_once()
        saved = json.loads(cursor.execute.call_args_list[-1][0][1][0])
        self.assertEqual(saved["requirements"]["clearance"]["level"], "Top Secret")


if __name__ == "__main__":
    unittest.main()
