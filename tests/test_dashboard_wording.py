import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

ROOT = Path(__file__).resolve().parents[1]
PAGES = ("index", "user-dashboard", "tuning", "rejected-listings", "credibility-scores")


def read(*parts):
    return ROOT.joinpath(*parts).read_text(encoding="utf-8")


class DashboardWording(unittest.TestCase):
    def test_section_headings_are_capitalized_and_locations_is_just_locations(self):
        html = read("templates", "user-dashboard.html")
        for heading in ("<h3>Job Titles", "<h3>Work Preferences", "<h3>Work History", "<h3>Skills", "Locations</label>"):
            self.assertIn(heading, html)
        self.assertNotIn("Search Locations + Radius", html)
        self.assertNotIn("<h3>Job titles", html)
        self.assertNotIn("<h3>Work preferences", html)

    def test_section_headings_are_a_size_bigger(self):
        self.assertIn(".profile-subsection h3 { margin:0 0 8px; font-size:1.4rem;", read("static", "css", "style.css"))

    def test_resume_is_spelled_with_its_accents_wherever_people_read_it(self):
        for page in PAGES:
            html = read("templates", f"{page}.html")
            self.assertNotRegex(html, r">\s*Resume Builder\s*<", page)
            self.assertIn("Résumé Builder", html, page)
        self.assertIn("Suggestions from Résumé:", read("static", "js", "charts.js"))

    def test_the_resume_builder_link_opens_in_the_same_tab(self):
        for page in PAGES:
            self.assertNotRegex(read("templates", f"{page}.html"), r'5001/"[^>]*target=', page)

    def test_search_settings_on_the_tuning_page_collapses(self):
        html = read("templates", "tuning.html")
        self.assertIn('<details class="settings-panel history-collapse" id="search-settings"', html)
        self.assertNotIn('<article class="settings-panel" id="search-settings">', html)

    def test_unsaved_profile_changes_are_warned_about(self):
        script = read("static", "js", "charts.js")
        for expected in ('addEventListener("beforeunload"', "event.preventDefault()", 'getElementById("unsaved-note")'):
            self.assertIn(expected, script)
        self.assertIn('id="unsaved-note"', read("templates", "user-dashboard.html"))


if __name__ == "__main__":
    unittest.main()
