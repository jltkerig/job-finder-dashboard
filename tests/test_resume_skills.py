import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import no_database  # noqa: F401  (cuts tests off from the real database)
from profile_tools import detect_skills, resume_skill_suggestions, skill_demand, uploaded_resume

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


if __name__ == "__main__":
    unittest.main()
