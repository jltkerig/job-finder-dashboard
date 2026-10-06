import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import no_database  # noqa: F401  (cuts tests off from the real database)
from jobfinder.search.relevance import expand_job_titles
from jobfinder.sources.employer_common import _arrangement


class SearchMatchingTests(unittest.TestCase):
    def test_related_titles_never_widen_to_the_bare_role(self):
        expanded = [title.casefold() for title in expand_job_titles(["UX Designer", "Product Designer"])]
        for wrong in ("designer", "fur designer", "textile designer"):
            self.assertNotIn(wrong, expanded)

    def test_related_titles_share_a_describing_word(self):
        expanded = expand_job_titles(["Web Developer"])
        self.assertTrue(all("web" in title.casefold() for title in expanded))

    def test_not_remote_is_onsite(self):
        self.assertEqual(_arrangement("Not Remote"), "Onsite")
        self.assertEqual(_arrangement("Non-Remote"), "Onsite")
        self.assertEqual(_arrangement("Fully Remote"), "Remote")


if __name__ == "__main__":
    unittest.main()
