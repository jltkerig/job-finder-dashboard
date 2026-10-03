import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

ROOT = Path(__file__).resolve().parents[1]
PAGES = ("index", "user-dashboard", "tuning", "rejected-listings", "credibility-scores")


def read(*parts):
    return ROOT.joinpath(*parts).read_text(encoding="utf-8")


class ApplyButton(unittest.TestCase):
    def test_each_listing_has_an_apply_button_that_names_the_job_for_claude_desktop(self):
        self.assertIn('class="bordered-button apply-action" type="button" data-job-id="{{ company.id }}"', read("templates", "index.html"))
        script = read("static", "js", "charts.js")
        self.assertIn("Job Finder job #${jobId}", script)  # Résumé Builder's get_job reads this id
        self.assertIn("read my writing rules", script)
        self.assertIn('window.location.href = "claude://"', script)


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
        self.assertNotIn("Suggestions from Résumé", read("static", "js", "charts.js"))  # one combined list now

    def test_the_resume_builder_link_opens_in_the_same_tab(self):
        for page in PAGES:
            self.assertNotRegex(read("templates", f"{page}.html"), r'5001/"[^>]*target=', page)

    def test_menu_order_has_resume_builder_before_settings_and_tuning(self):
        import re
        for page in PAGES:
            labels = re.findall(r'<a class="nav-link[^"]*" href="[^"]*">([^<]+)</a>', read("templates", f"{page}.html"))
            self.assertEqual(labels[:5], ["Search", "Dashboard", "Résumé Builder", "Settings", "Tuning"], page)

    def test_search_settings_on_the_tuning_page_collapses(self):
        html = read("templates", "tuning.html")
        self.assertIn('<details class="settings-panel history-collapse" id="search-settings"', html)
        self.assertNotIn('<article class="settings-panel" id="search-settings">', html)

    def test_tuning_checkboxes_lead_their_setting_and_the_save_button_clears_the_text(self):
        html = read("templates", "tuning.html")
        self.assertIn('<label class="switch-row"><input type="checkbox" name="{{ key }}"', html)  # the box comes first, then the words
        css = read("static", "css", "style.css")
        self.assertIn("#search-settings form .switch-row { display: flex;", css)
        self.assertIn('#search-settings form .switch-row input[type="checkbox"] { width: 18px;', css)  # not the full-width input style
        self.assertIn("#search-settings form .primary-action { display: block; margin: 28px 0 0; }", css)

    def test_the_search_location_box_wording(self):
        for page in ("index", "user-dashboard"):
            html = read("templates", f"{page}.html")
            self.assertIn(">Add Location</button>", html)
            self.assertNotIn("Add City", html)
        html = read("templates", "index.html")
        self.assertIn('<label for="search-city-input">City, Zip, Country, State</label>', html)
        self.assertIn(".city-search-field .city-list-below:empty { display: none; }", read("static", "css", "style.css"))

    def test_explanations_sit_under_the_label_not_below_the_box(self):
        import re
        dash = read("templates", "user-dashboard.html")
        self.assertNotIn("Job Finder searches for every title here.", dash)
        self.assertNotIn("Shown below your photo", dash)  # can be inferred
        self.assertNotIn("Choose any types and locations you want", dash)
        for label_id, text in (("job-titles", "Separate titles with commas."),):
            self.assertRegex(dash, r'<label for="' + label_id + r'">[^<]*</label>\s*<p class="field-help">' + re.escape(text))
        search = read("templates", "index.html")
        for label_id, text in (("job-title", "Separate multiple titles"), ("search-city-input", "Add a city for a radius search")):
            self.assertRegex(search, r'(?s)<label for="' + label_id + r'">[^<]*</label>(?:(?!</div>).)*?<p [^>]*class="field-help"[^>]*>\s*' + re.escape(text))
        # the compact filter row keeps its one explanation as the box's hover tip
        self.assertIn('<label for="result-min-credibility" title="Only individual openings', search)
        self.assertNotIn("Search by job title and city radius or an entire state.", search)
        tuning = read("templates", "tuning.html")
        self.assertRegex(tuning, r'<label for="tune-\{\{ key \}\}">[^<]*</label>\s*<p class="field-help">')

    def test_every_section_on_the_settings_page_collapses(self):
        import re
        html = read("templates", "rejected-listings.html")
        self.assertNotIn('<section class="settings-panel', html)
        self.assertNotIn('<section class="company-section settings-panel', html)
        panels = re.findall(r'<details class="[^"]*history-collapse" id="([a-z-]+)" data-remember="(jobFinder\.\w+)"', html)
        self.assertEqual([p[0] for p in panels], ["rejected-listings", "search-skips", "blocked-domains", "blocked-companies"])
        self.assertEqual(len({p[1] for p in panels}), 4)  # each remembers its own choice
        self.assertEqual(html.count("<summary><h2>"), 4)
        # the notice after blocking something is seen, and a link to a panel opens it
        self.assertIn("{% if domain_notice %} data-open-now{% endif %}", html)
        self.assertIn("{% if company_notice %} data-open-now{% endif %}", html)
        self.assertIn('linked.matches("details[data-remember]")', read("static", "js", "charts.js"))

    def test_unsaved_profile_changes_are_warned_about(self):
        script = read("static", "js", "charts.js")
        for expected in ('addEventListener("beforeunload"', "event.preventDefault()", 'getElementById("unsaved-note")'):
            self.assertIn(expected, script)
        self.assertIn('id="unsaved-note"', read("templates", "user-dashboard.html"))


if __name__ == "__main__":
    unittest.main()
