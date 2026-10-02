import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import no_database  # noqa: F401  (cuts tests off from the real database)
from jobfinder.profiles.profile_tools import detect_skills, parse_work_history, resume_skill_suggestions, resume_suggestions, skill_demand, uploaded_resume

RESUME = """Jane Doe
Web Designer with HTML, CSS and JavaScript. Built WordPress sites with Bootstrap and Adobe InDesign layouts."""


class ResumeSkills(unittest.TestCase):
    def folder(self, text=RESUME, name="My_Resume.pdf"):
        directory = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(directory, ignore_errors=True))
        if text is not None:
            (directory / "text.txt").write_text(text, encoding="utf-8")
        if name is not None:
            (directory / "info.json").write_text(json.dumps({"original_name": name}), encoding="utf-8")
        return directory

    def test_skills_in_the_uploaded_resume_are_suggested(self):
        name, skills = resume_skill_suggestions(self.folder(), [])
        self.assertEqual(name, "My_Resume.pdf")
        for expected in ("HTML", "CSS", "JavaScript", "WordPress", "Bootstrap", "Adobe InDesign"):
            self.assertIn(expected, skills)

    def test_skills_already_in_the_profile_are_left_out_whatever_the_case(self):
        _, skills = resume_skill_suggestions(self.folder(), ["html", "WORDPRESS"])
        self.assertNotIn("HTML", skills)
        self.assertNotIn("WordPress", skills)
        self.assertIn("CSS", skills)

    def test_no_uploaded_resume_means_no_suggestions_and_no_error(self):
        self.assertEqual(resume_skill_suggestions(self.folder(text=None), []), ("", []))
        self.assertEqual(resume_skill_suggestions(Path(tempfile.gettempdir()) / "no-such-folder-here", []), ("", []))
        self.assertEqual(resume_skill_suggestions(self.folder(text="   \n"), []), ("", []))

    def test_a_missing_info_file_still_works(self):
        name, text = uploaded_resume(self.folder(name=None))
        self.assertEqual(name, "your résumé")
        self.assertIn("WordPress", text)

    def test_the_dashboard_page_has_a_place_for_the_chips_and_the_script_fills_it(self):
        root = Path(__file__).resolve().parents[1]
        self.assertIn('id="resume-skill-suggestions"', (root / "templates" / "user-dashboard.html").read_text(encoding="utf-8"))
        self.assertIn("function showResumeSkills", (root / "static" / "js" / "charts.js").read_text(encoding="utf-8"))


class MoreSuggestions(unittest.TestCase):
    def test_the_wider_skill_list_picks_up_what_a_designer_resume_says(self):
        text = ("Built landing pages and emails; supported A/B testing and SMS marketing. Product photography, image editing "
                "and photo manipulation. Graphic design and web design for marketing campaigns. Frontend Web Developer.")
        found = detect_skills(text)
        for expected in ("Landing Pages", "A/B Testing", "SMS Marketing", "Photography", "Photo Editing", "Graphic Design",
                         "Web Design", "Web Development"):
            self.assertIn(expected, found)

    def test_a_bare_qa_is_still_not_a_skill(self):
        self.assertNotIn("QA Testing", detect_skills("Email QA in Litmus."))
        self.assertIn("QA Testing", detect_skills("Hands-on QA testing of campaigns."))

    def test_skills_the_listings_keep_asking_for_are_ranked_by_how_many_name_them(self):
        lists = ['["Figma", "SEO", "HTML"]', '["Figma", "Accessibility"]', '["figma", "SEO"]', "not json", None, '[]']
        self.assertEqual(skill_demand(lists, ["HTML"]), [("Figma", 3), ("SEO", 2), ("Accessibility", 1)])

    def test_demand_leaves_out_saved_skills_in_any_case_and_respects_the_limit(self):
        lists = ['["Figma", "SEO", "Git"]'] * 2
        self.assertEqual(skill_demand(lists, ["figma"]), [("Git", 2), ("SEO", 2)])
        self.assertEqual(len(skill_demand(lists, [], limit=1)), 1)
        self.assertEqual(skill_demand([], []), [])

    def test_the_page_has_a_place_for_the_job_demand_chips(self):
        root = Path(__file__).resolve().parents[1]
        self.assertIn('id="demand-skill-suggestions"', (root / "templates" / "user-dashboard.html").read_text(encoding="utf-8"))


HISTORY = """Jane Doe
Professional Summary
Ten years of web work.
Employment History
Web Designer
Acme Widgets
Remote
January 2025 - May 2025
●
Rebuilt web pages using Bootstrap.
●
Applied design expertise to a large-scale site migration across
several client websites.

Front End Web Developer
Sample Corp LLC
Baltimore, MD
May 2015 – Present
●
Designed landing pages and emails.
Junior Web Designer
Example Studio

Springfield, MD
2014 - 2015
Education History
State University
Towson, MD
Bachelor of Science, Graphic Design
"""


class WorkHistory(unittest.TestCase):
    def test_jobs_are_read_with_title_company_dates_and_bullets(self):
        jobs = parse_work_history(HISTORY)
        self.assertEqual([(job["role"], job["company"]) for job in jobs],
                         [("Web Designer", "Acme Widgets"), ("Front End Web Developer", "Sample Corp LLC"),
                          ("Junior Web Designer", "Example Studio")])
        self.assertEqual([job["dates"] for job in jobs], ["2025-01 – 2025-05", "2015-05 – Present", "2014 – 2015"])
        self.assertEqual(jobs[0]["description"],
                         "• Rebuilt web pages using Bootstrap.\n• Applied design expertise to a large-scale site migration across several client websites.")

    def test_a_jobs_text_does_not_include_the_next_jobs_heading(self):
        jobs = parse_work_history(HISTORY)
        self.assertNotIn("Front End", jobs[0]["description"])
        self.assertNotIn("Junior", jobs[1]["description"])
        self.assertEqual(jobs[2]["description"], "")

    def test_places_are_not_taken_for_companies_and_education_is_not_a_job(self):
        jobs = parse_work_history(HISTORY)
        self.assertNotIn("Remote", [job["company"] for job in jobs])
        self.assertNotIn("Springfield, MD", [job["company"] for job in jobs])
        self.assertEqual(len(jobs), 3)  # State University is under Education

    def test_no_experience_heading_or_no_dates_means_no_jobs(self):
        self.assertEqual(parse_work_history("Skills\nHTML, CSS"), [])
        self.assertEqual(parse_work_history("Employment History\nWeb Designer\nAcme Widgets"), [])

    def test_invisible_characters_from_pdfs_are_ignored(self):
        text = "Work Experience\nWeb Designer\u200b \nAcme Widgets\u200b\nMarch 2020 - June 2021\u200b"
        [job] = parse_work_history(text)
        self.assertEqual((job["role"], job["company"], job["dates"]), ("Web Designer", "Acme Widgets", "2020-03 – 2021-06"))

    def test_the_resume_upload_review_uses_the_same_reader(self):
        self.assertEqual([job["role"] for job in resume_suggestions(HISTORY)["work_history"]],
                         ["Web Designer", "Front End Web Developer", "Junior Web Designer"])

    def test_the_form_is_filled_in_only_through_the_page_script(self):
        root = Path(__file__).resolve().parents[1]
        self.assertIn('id="work-history-note"', (root / "templates" / "user-dashboard.html").read_text(encoding="utf-8"))
        self.assertIn("Filled in from your r", (root / "static" / "js" / "charts.js").read_text(encoding="utf-8"))


class WorkHistoryCards(unittest.TestCase):
    def test_each_job_is_a_card_with_dates_a_preview_and_done_and_remove_buttons(self):
        root = Path(__file__).resolve().parents[1]
        script = (root / "static" / "js" / "charts.js").read_text(encoding="utf-8")
        for expected in ('"work-entry"', '"work-dates"', '"work-preview"', '"Remove Job"', '"Done"', "No jobs added yet"):
            self.assertIn(expected, script)
        css = (root / "static" / "css" / "style.css").read_text(encoding="utf-8")
        for expected in (".work-entry summary", ".work-fields", ".work-dates", ".work-preview"):
            self.assertIn(expected, css)


if __name__ == "__main__":
    unittest.main()
