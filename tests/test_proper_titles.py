import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import no_database  # noqa: F401  (cuts tests off from the real database)
from jobfinder.web import profile_store
from owners import web_source
import dashboard
from jobfinder.profiles.onet_data import proper_title, related_title_suggestions, title_matches


class ProperTitles(unittest.TestCase):
    def test_titles_get_proper_capitalization(self):
        for raw, expected in {"ux/ui designer": "UX/UI Designer", "front-end developer": "Front-End Developer",
                              "director of marketing": "Director of Marketing", "wordpress developer ii": "WordPress Developer II",
                              "qa analyst": "QA Analyst", "senior ios developer": "Senior iOS Developer",
                              "  web   designer ": "Web Designer", "WEB DESIGNER": "Web Designer"}.items():
            self.assertEqual(proper_title(raw), expected)

    def test_every_suggestion_is_properly_capitalized(self):
        suggestions = title_matches("graphic des") + related_title_suggestions("web designer") + \
            profile_store.related_job_title_suggestions("front end developer, ux designer")
        self.assertTrue(suggestions)
        for title in suggestions:
            self.assertEqual(title, proper_title(title), title)
            self.assertTrue(title[0].isupper(), title)

    def test_what_is_typed_in_any_case_finds_the_same_titles(self):
        self.assertEqual(title_matches("GRAPHIC DES"), title_matches("graphic des"))
        self.assertEqual(related_title_suggestions("Web Designer"), related_title_suggestions("WEB designer"))


class SavedTitles(unittest.TestCase):
    def test_saving_a_profile_writes_titles_properly_capitalized(self):
        source = web_source()
        self.assertIn('title = proper_title(part.strip()[:255])', source)
        self.assertIn('primary = proper_title(request.form.get("primary_job_title", "").strip()[:255])', source)
        self.assertIn('title = proper_title(str(data.get("title", "")).strip()[:255])', source)

    def test_the_profile_is_shown_with_proper_capitalization(self):
        source = web_source()
        self.assertIn('profile["job_titles"] = list(dict.fromkeys(proper_title(title) for title in profile["job_titles"]))', source)


if __name__ == "__main__":
    unittest.main()
