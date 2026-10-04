import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import no_database  # noqa: F401  (cuts tests off from the real database)
from jobfinder.profiles.profile_tools import resume_suggestions


class ResumeSuggestions(unittest.TestCase):
    def test_layout_text_keeps_name_and_month_based_work_history(self):
        resume = """Jane       Doe
Professional Summary
Web designer with HTML and CSS experience.
Employment History
Web Designer
Acme Widgets
Remote
January 2025 - May 2025
Built pages.
Front End Web Developer
Sample Corp LLC
Baltimore, MD
May 2015 – October 2024
Built landing pages.
Education History
"""
        result = resume_suggestions(resume)
        self.assertEqual((result["first_name"], result["last_name"]), ("Jane", "Doe"))
        self.assertEqual([(job["role"], job["company"]) for job in result["work_history"]],
                         [("Web Designer", "Acme Widgets"), ("Front End Web Developer", "Sample Corp LLC")])


if __name__ == "__main__":
    unittest.main()
