import json
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import no_database  # noqa: F401  (cuts tests off from the real database)
from jobfinder.profiles.requirements import key_lines, posting_body, listing_requirements, requirement_gaps, skill_sections, weighted_fit
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

    def test_focus_area_the_profile_never_mentions(self):
        text = "Design fashion accessories. Know the fashion calendar. Love apparel, footwear and fashion week."
        self.assertIn("Built around fashion work", gaps(text)[0]["text"])
        self.assertEqual(gaps(text, dict(PROFILE, skills=["Fashion illustration"])), [])

    def test_avoided_work(self):
        profile = dict(PROFILE, avoid_terms=["video editing", "audio"])
        found = gaps("You will handle Video  Editing and audio mixing for our podcasts.", profile)
        self.assertEqual([gap["text"] for gap in found if gap.get("avoid")],
                         ["Mentions video editing, which you avoid", "Mentions audio, which you avoid"])
        self.assertEqual(gaps("Audiophile welcome.", profile), [])

    def test_long_experience_area(self):
        found = gaps("4-5 years of SQL database administration and programming experience required.")
        self.assertIn("Asks for 4+ years of sql database administration programming experience", [gap["text"] for gap in found])

    def test_key_lines(self):
        text = ("Your Impact\n• Lead design reviews for the team\nQualifications\n• Bachelor's Degree in Design\n"
                "Nice to have:\n• Motion design skills")
        self.assertEqual(key_lines(text), {"Requirements": ["Bachelor's Degree in Design"],
                                           "Responsibilities": ["Lead design reviews for the team"],
                                           "Nice to Have": ["Motion design skills"]})

    def test_posting_body_drops_menus(self):
        text = ("Skip to main content\nLanguage\nDeutsch\nCareers\nJobs\nDesigner\n"
                "We are looking for a designer to join our small and friendly team.")
        self.assertTrue(posting_body(text).startswith("Careers"))

    def test_skill_sections(self):
        text = "We use Figma.\nRequirements:\nHTML and CSS\nNice to have:\nReact\nPhotoshop is a plus."
        self.assertEqual(skill_sections(text, ["HTML", "CSS", "React", "Photoshop", "Figma"]),
                         {"HTML": "required", "CSS": "required", "React": "preferred", "Photoshop": "preferred",
                          "Figma": "other"})

    def test_missing_nice_to_have_costs_less(self):
        sections = {"HTML": "required", "React": "preferred"}
        self.assertGreater(weighted_fit(["HTML"], sections)["score"], weighted_fit(["React"], sections)["score"])
        self.assertEqual(weighted_fit(["React"], sections)["missing_required"], ["HTML"])


class RouteTests(unittest.TestCase):
    def test_reads_page_once_and_keeps_requirements(self):
        cursor = MagicMock()
        cursor.fetchall.return_value = [{"id": 7, "career_url": "https://example.com/job", "source_url": "",
                                         "listing_details": "{}", "listing_skills": '["Figma", "HTML"]'}]
        connection = MagicMock()
        connection.cursor.return_value = cursor
        about = "<p>" + "We build friendly tools for small teams and care about clear, accessible design. " * 4 + "</p>"
        page = MagicMock(text=about + "<p>Active Top Secret clearance required.</p><h3>Requirements:</h3><p>HTML</p>"
                                  "<h3>Nice to have:</h3><p>Figma</p>")
        client = dashboard.app.test_client()
        with client.session_transaction() as session:
            session["csrf_token"] = "t"
        with patch("jobfinder.web.top_picks_routes.db.connect", return_value=connection), \
                patch("jobfinder.search.relevance.fetch_text", return_value=page) as fetch, \
                patch("jobfinder.web.profile_store.get_user_profile", return_value=PROFILE):
            response = client.post("/top-picks/requirements", json={"company_ids": [7]}, headers={"X-CSRF-Token": "t"})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["gaps"]["7"]["items"][0]["hard"])
        fit = response.get_json()["gaps"]["7"]["fit"]
        self.assertEqual(fit["missing_required"], ["HTML"])
        fetch.assert_called_once()
        saved = json.loads(cursor.execute.call_args_list[-1][0][1][0])
        self.assertEqual(saved["requirements"]["clearance"]["level"], "Top Secret")


class PostingUrlTests(unittest.TestCase):
    def test_icims_frame_is_read_first(self):
        from jobfinder.web.top_picks_routes import posting_urls
        url = "https://careers-acme.icims.com/jobs/1/designer/job"
        self.assertEqual(posting_urls(url), [url + "?in_iframe=1", url])
        self.assertEqual(posting_urls("https://acme.com/jobs/1"), ["https://acme.com/jobs/1"])

    def test_page_shell_counts_as_unread(self):
        from jobfinder.web.top_picks_routes import _requirements_for
        row = {"id": 1, "career_url": "https://acme.com/job", "source_url": "", "listing_details": "{}", "listing_skills": "[]"}
        with patch("jobfinder.search.relevance.fetch_text", return_value=MagicMock(text="<p>Privacy Policy - Acme</p>")):
            self.assertIsNone(_requirements_for(row, MagicMock(), MagicMock()))


if __name__ == "__main__":
    unittest.main()
