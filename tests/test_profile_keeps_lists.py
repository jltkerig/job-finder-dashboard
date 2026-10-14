import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import no_database  # noqa: F401  (cuts tests off from the real database)
import dashboard
from jobfinder.web import profile_store

SAVED = {"first_name": "Jane", "last_name": "Doe", "state": "MD", "skills": ["HTML"], "work_history": [],
         "education": [{"school": "Springfield University", "degree": "Bachelor's Degree"}]}


class ProfileFormKeepsWhatItDoesNotCarry(unittest.TestCase):
    def post(self, **extra):
        client = dashboard.app.test_client()
        with client.session_transaction() as session:
            session["csrf_token"] = "t"
        data = {"csrf_token": "t", "first_name": "Jane", "last_name": "Doe", "state": "MD", "job_title": "Web Designer", **extra}
        with patch.object(profile_store, "get_user_profile", return_value=SAVED), \
                patch.object(profile_store, "save_user_profile", return_value=True) as save:
            client.post("/save-profile", data=data)
        return save.call_args.kwargs

    def test_a_form_without_the_boxes_leaves_skills_jobs_and_schools_alone(self):
        sent = self.post()
        self.assertIsNone(sent["education"])
        self.assertIsNone(sent["skills"])
        self.assertIsNone(sent["work_history"])

    def test_a_form_that_carries_the_boxes_replaces_them(self):
        sent = self.post(education_json="[]", skills_json='["HTML", "CSS"]', work_history_json="[]")
        self.assertEqual(sent["education"], [])
        self.assertEqual(sent["skills"], ["HTML", "CSS"])
        self.assertEqual(sent["work_history"], [])


if __name__ == "__main__":
    unittest.main()
