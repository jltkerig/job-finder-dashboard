import json
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import no_database  # noqa: F401  (cuts tests off from the real database)
import dashboard

PROFILE = {"first_name": "A", "last_name": "B", "state": "MD", "job_titles": ["Designer"], "cities": [],
           "skills": ["Figma", "HTML"]}


def client():
    test_client = dashboard.app.test_client()
    with test_client.session_transaction() as session:
        session["csrf_token"] = "t"
    return test_client


class SkillBubbleTests(unittest.TestCase):
    def test_preview_lists_the_skills_with_which_ones_you_have(self):
        from datetime import date
        from jobfinder.profiles import requirements as reqs
        sections = {"Figma": "required", "React": "required", "Photoshop": "preferred", "SQL": "other"}
        details = {"requirements": {"version": reqs.VERSION, "text": "x" * 700, "skill_sections": sections,
                                    "read_on": date.today().isoformat()}}
        cursor = MagicMock()
        cursor.fetchone.return_value = {"id": 7, "career_url": "", "source_url": "",
                                        "listing_details": json.dumps(details), "listing_skills": "[]"}
        connection = MagicMock()
        connection.cursor.return_value = cursor
        with patch("jobfinder.web.top_picks_routes.db.connect", return_value=connection), \
                patch("jobfinder.web.profile_store.get_user_profile", return_value=PROFILE):
            data = client().get("/listing-preview/7").get_json()
        skills = {skill["name"]: skill for skill in data["skills"]}
        self.assertTrue(skills["Figma"]["have"])
        self.assertFalse(skills["React"]["have"])
        self.assertEqual([skill["where"] for skill in data["skills"]], ["required", "required", "preferred", "other"])

    def test_clicking_a_bubble_adds_the_skill_once(self):
        saved = []
        profile = dict(PROFILE, skills=list(PROFILE["skills"]))
        with patch("jobfinder.web.profile_store.get_user_profile", return_value=profile), \
                patch("jobfinder.web.profile_store.save_user_profile", side_effect=lambda *a, **k: saved.append(k) or True), \
                patch("jobfinder.web.profile_store.profile_version", return_value="v2"), \
                patch("jobfinder.web.profile_routes.refresh_job_fit", return_value=0):
            first = client().post("/profile/add-skill", json={"skill": "react"}, headers={"X-CSRF-Token": "t"})
            again = client().post("/profile/add-skill", json={"skill": "figma"}, headers={"X-CSRF-Token": "t"})
        self.assertEqual(first.get_json()["status"], "saved")
        self.assertEqual(saved[0]["skills"], ["Figma", "HTML", "React"])
        self.assertEqual(again.get_json()["status"], "already_saved")
        self.assertEqual(len(saved), 1)

    def test_an_empty_skill_is_refused(self):
        response = client().post("/profile/add-skill", json={"skill": "  "}, headers={"X-CSRF-Token": "t"})
        self.assertEqual(response.status_code, 400)


if __name__ == "__main__":
    unittest.main()
