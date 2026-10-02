import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import no_database  # noqa: F401  (cuts tests off from the real database)
from profile_tools import resume_skill_suggestions, uploaded_resume

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


if __name__ == "__main__":
    unittest.main()
