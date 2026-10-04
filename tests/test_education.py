import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import no_database  # noqa: F401  (cuts tests off from the real database)
from jobfinder.web.profile_store import clean_education
from jobfinder.web.schema import EDUCATION_DEGREES, EDUCATION_FIELDS

ROOT = Path(__file__).resolve().parents[1]


class Education(unittest.TestCase):
    def test_entries_are_trimmed_and_degrees_come_from_the_list(self):
        rows = clean_education([
            {"school": " State University ", "degree": "Bachelor's Degree", "major": "Graphic Design", "gpa": "3.5",
             "start_date": "2010-09", "end_date": "2013-05"},
            {"school": "Cecil College", "degree": "Made-up Degree"},
            {"school": "", "degree": "GED"},  # no school: dropped
            "not a school",
        ])
        self.assertEqual([r["school"] for r in rows], ["State University", "Cecil College"])
        self.assertEqual(rows[0]["gpa"], "3.5")
        self.assertEqual(rows[1]["degree"], "Other")
        self.assertEqual(set(rows[0]), {name for name, _ in EDUCATION_FIELDS})

    def test_the_dashboard_has_an_education_section_with_the_degree_list(self):
        page = (ROOT / "templates" / "user-dashboard.html").read_text(encoding="utf-8")
        self.assertIn('name="education_json"', page)
        self.assertIn("data-degrees='{{ (education_degrees or [])|tojson }}'", page)
        self.assertIn("Bachelor's Degree", EDUCATION_DEGREES)
        script = (ROOT / "static" / "js" / "charts.js").read_text(encoding="utf-8")
        self.assertIn('"Currently attending"', script)

    def test_resume_builder_offers_the_same_degrees(self):
        builder = ROOT / "resume-builder" / "jobfinder_db.py"
        if not builder.exists():
            self.skipTest("Résumé Builder is not inside Job Finder")
        text = builder.read_text(encoding="utf-8")
        for degree in EDUCATION_DEGREES:
            self.assertIn(f'"{degree}"', text)


if __name__ == "__main__":
    unittest.main()
