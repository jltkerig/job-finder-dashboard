import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import no_database  # noqa: F401  (cuts tests off from the real database)
import dashboard
from onet_data import proper_title, related_title_suggestions, title_matches


class ProperTitles(unittest.TestCase):
    def test_titles_get_proper_capitalization(self):
        for raw, expected in {"ux/ui designer": "UX/UI Designer", "front-end developer": "Front-End Developer",
                              "director of marketing": "Director of Marketing", "wordpress developer ii": "WordPress Developer II",
                              "qa analyst": "QA Analyst", "senior ios developer": "Senior iOS Developer",
                              "  web   designer ": "Web Designer", "WEB DESIGNER": "Web Designer"}.items():
            self.assertEqual(proper_title(raw), expected)

    def test_every_suggestion_is_properly_capitalized(self):
        suggestions = title_matches("graphic des") + related_title_suggestions("web designer") + \
            dashboard.related_job_title_suggestions("front end developer, ux designer")
        self.assertTrue(suggestions)
        for title in suggestions:
            self.assertEqual(title, proper_title(title), title)
            self.assertTrue(title[0].isupper(), title)

    def test_what_is_typed_in_any_case_finds_the_same_titles(self):
        self.assertEqual(title_matches("GRAPHIC DES"), title_matches("graphic des"))
        self.assertEqual(related_title_suggestions("Web Designer"), related_title_suggestions("WEB designer"))


if __name__ == "__main__":
    unittest.main()
