import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import no_database  # noqa: F401  (cuts tests off from the real database)
from jobfinder.search.relevance import expand_job_titles, is_irrelevant_lead

TITLES = expand_job_titles(["Web Designer", "Graphic Designer", "Visual Designer", "Frontend Developer", "Content Designer"])


def lead(title, **extra):
    return dict({"career_job_title": title, "is_kept": 0, "source_type": "SearXNG"}, **extra)


class WrongRoleTests(unittest.TestCase):
    def test_other_fields_are_rejected_even_with_an_old_stored_match(self):
        for title in ("Engineer III - Sewer Design", "Telecommunications BIM Designer", "Computer-Aided Designer",
                      "Technical Designer - Accessories", "Senior Java & React Developer", "Instructional Designer"):
            self.assertTrue(is_irrelevant_lead(lead(title), {"matched_title": "Designer"}, TITLES), title)

    def test_matching_and_design_adjacent_titles_stay(self):
        for title in ("Senior Web Designer", "UX/UI Designer", "Digital Content Specialist", "Senior Creative Designer II",
                      "Graphic Designer", "Adjunct Faculty, Graphic Design", "Lead, Digital Designer (Apparel & Footwear)"):
            self.assertFalse(is_irrelevant_lead(lead(title), {}, TITLES), title)

    def test_saved_jobs_are_never_rejected(self):
        self.assertFalse(is_irrelevant_lead(lead("Engineer I - Highway Design", is_kept=1), {}, TITLES))

    def test_related_titles_add_no_unrelated_words(self):
        self.assertFalse(any("react" in t.casefold() or "computer" in t.casefold() for t in TITLES))


if __name__ == "__main__":
    unittest.main()
