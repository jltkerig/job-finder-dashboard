import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import no_database  # noqa: F401  (cuts tests off from the real database)
from jobfinder.profiles.profile_tools import related_skills


class RelatedSkills(unittest.TestCase):
    def test_html_brings_up_css_and_responsive_design(self):
        found = related_skills("HTML")
        for skill in ("CSS", "Responsive Design", "JavaScript"):
            self.assertIn(skill, found)

    def test_any_spelling_of_the_skill_works_and_it_never_suggests_itself(self):
        self.assertIn("CSS", related_skills("html"))
        self.assertNotIn("HTML", related_skills("html"))

    def test_the_jobs_found_add_skills_that_appear_alongside(self):
        listings = [json.dumps(["Zendesk", "Intercom", "HTML"]), json.dumps(["Zendesk", "HTML"]), json.dumps(["Photoshop"])]
        found = related_skills("HTML", listings)
        self.assertIn("Zendesk", found)
        self.assertLess(found.index("CSS"), found.index("Zendesk"))  # the curated list comes first

    def test_an_unknown_word_with_no_listings_gives_nothing(self):
        self.assertEqual(related_skills("zzzz"), [])
        self.assertEqual(related_skills(""), [])

    def test_the_page_asks_for_related_skills_as_you_type(self):
        root = Path(__file__).resolve().parents[1]
        self.assertIn("/skill-related?q=", (root / "static" / "js" / "charts.js").read_text(encoding="utf-8"))
        self.assertIn('"/skill-related"', (root / "jobfinder" / "web" / "profile_routes.py").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
