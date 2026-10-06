import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import no_database  # noqa: F401  (cuts tests off from the real database)
from jobfinder.search.company_names import tidy_company_name
from jobfinder.search.usa_location import find_state_from_text
from jobfinder.sources.job_listings import looks_like_not_a_job
from jobfinder.web.listing_queries import merge_duplicates


class ListingCleanupTests(unittest.TestCase):
    def test_washington_dc_is_not_washington_state(self):
        self.assertEqual(find_state_from_text("Washington, DC"), "DC")
        self.assertEqual(find_state_from_text("Washington D.C. 20006"), "DC")
        self.assertEqual(find_state_from_text("Seattle, Washington"), "WA")

    def test_pages_that_are_not_one_job(self):
        for title in ("Find a Web Designer in Maryland", "Remote Content Designer Jobs in the US",
                      "WordPress web design in Gaithersburg, MD - FreshySites", "Top Maryland Web Design Company"):
            self.assertTrue(looks_like_not_a_job(title), title)
        for title in ("Web Designer", "Charter Global is hiring: Graphic Designer in Baltimore"):
            self.assertFalse(looks_like_not_a_job(title), title)

    def test_company_names(self):
        self.assertEqual(tidy_company_name("9025 CVS Shared Services"), "CVS Shared Services")
        self.assertEqual(tidy_company_name("84 Lumber"), "84 Lumber")
        self.assertEqual(tidy_company_name("Coda Search│Staffing"), "Coda Search")
        self.assertEqual(tidy_company_name("Production Designer", "Production Designer"), "")

    def test_one_row_per_job_found_on_several_boards(self):
        rows = [{"id": 1, "name": "Sinclair Broadcast Group", "career_job_title": "Digital Designer, Podcasts",
                 "source_type": "National Labor Exchange", "career_credibility": 3, "source_url": "https://a"},
                {"id": 2, "name": "Sinclair", "career_job_title": "Digital Designer, Podcasts",
                 "source_type": "Employer careers", "career_credibility": 8, "source_url": "https://b"},
                {"id": 3, "name": "Sinclair", "career_job_title": "Digital Producer", "source_type": "LinkedIn"}]
        merged = merge_duplicates(rows)
        self.assertEqual([row["id"] for row in merged], [2, 3])
        self.assertEqual(merged[0]["also_listed"], [{"id": 1, "source": "National Labor Exchange", "url": "https://a"}])


if __name__ == "__main__":
    unittest.main()
