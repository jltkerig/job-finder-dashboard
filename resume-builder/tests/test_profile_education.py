"""Tests with made-up data only. Every folder is temporary; the database is never touched."""
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_resume_builder  # noqa: E402,F401  (sets the temporary folders before the app's config is read)
import jobfinder_db  # noqa: E402
import profile_import  # noqa: E402

RESUME = """Jane Example
Springfield, MD

Education History
Springfield University
Springfield, MD
Bachelor of Science, Graphic Design
Riverside College
Riverside, MD
Associate Degree, Graphic Design
Additional Experience
Volunteer
"""


def profile(education=()):
    return {"first_name": "Jane", "last_name": "Example", "home_location": "Springfield, MD", "primary_job_title": "",
            "job_titles": [], "skills": [], "work_history": [], "education": list(education)}


class EducationFromResume(unittest.TestCase):
    def test_schools_in_the_resume_are_found(self):
        found = profile_import.extract(RESUME)["education"]
        self.assertEqual([(s["school"], s["degree"], s["major"]) for s in found],
                         [("Springfield University", "Bachelor's Degree", "Graphic Design"),
                          ("Riverside College", "Associate's Degree", "Graphic Design")])

    def test_only_schools_the_profile_lacks_are_offered(self):
        have = [{"school": "springfield university", "degree": "Bachelor's Degree"}]
        offered = profile_import.new_schools(RESUME, profile(have))
        self.assertEqual([s["school"] for s in offered], ["Riverside College"])

    def test_filling_the_profile_adds_the_missing_schools_and_keeps_the_rest(self):
        have = [{"school": "Springfield University", "degree": "Bachelor's Degree", "major": "Art", "minor": "",
                 "start_date": "", "end_date": "", "gpa": ""}]
        with mock.patch.object(jobfinder_db, "get_profile", return_value=profile(have)), \
                mock.patch.object(jobfinder_db, "save_profile") as save:
            added = profile_import.fill_profile(RESUME)
        self.assertIn("1 school", added)
        saved_schools = save.call_args.args[7]
        self.assertEqual([s["school"] for s in saved_schools], ["Springfield University", "Riverside College"])
        self.assertEqual(saved_schools[0]["major"], "Art")  # what was already there is untouched

    def test_nothing_new_leaves_education_alone(self):
        have = profile_import.extract(RESUME)["education"]
        with mock.patch.object(jobfinder_db, "get_profile", return_value=profile(have)), \
                mock.patch.object(jobfinder_db, "save_profile") as save:
            added = profile_import.fill_profile(RESUME)
        self.assertNotIn("school", " ".join(added))
        if save.called:
            self.assertEqual(len(save.call_args.args), 7)  # no education argument: what is saved is left as it is


if __name__ == "__main__":
    unittest.main()
