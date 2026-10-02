import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import no_database  # noqa: F401  (cuts tests off from the real database)
import dashboard


class FitProfileForTheExtension(unittest.TestCase):
    def get(self, profile):
        with patch.object(dashboard, "get_user_profile", return_value=profile):
            response = dashboard.app.test_client().get("/extension/fit-profile")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("Access-Control-Allow-Origin", response.headers)  # web pages can't read it
        return response.get_json()

    def test_primary_title_first_without_repeats(self):
        with patch.object(dashboard, "get_blocked_companies", return_value=["Bark"]):
            data = self.get({"primary_job_title": "Web Designer", "job_titles": ["web designer", "Web Producer"],
                             "skills": ["HTML"], "work_preferences": ["Remote"]})
        self.assertEqual(data["blocked_companies"], ["Bark"])
        self.assertEqual(data["titles"], ["Web Designer", "Web Producer"])
        self.assertEqual(data["skills"], ["HTML"])
        self.assertEqual(data["work_preferences"], ["Remote"])
        self.assertIn("HTML", data["skill_aliases"])
        self.assertIn("React", data["ambiguous_skills"])

    def test_typos_in_titles_are_fixed_like_in_searches(self):
        data = self.get({"primary_job_title": "", "job_titles": ["production specalist"], "skills": [], "work_preferences": []})
        self.assertEqual(data["titles"], ["production specialist"])

    def test_empty_profile(self):
        data = self.get({"primary_job_title": "", "job_titles": [], "skills": [], "work_preferences": []})
        self.assertEqual(data["titles"], [])


if __name__ == "__main__":
    unittest.main()
