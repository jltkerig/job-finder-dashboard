import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import no_database  # noqa: F401  (cuts tests off from the real database)
from jobfinder.sources import board_status as module
from jobfinder.sources.board_status import board_status

JOB = "cdb5cf24-6002-48cc-9779-0b6276175853"
URL = f"https://jobs.ashbyhq.com/netgear/{JOB}"


def http_with(jobs, status=200):
    response = MagicMock(status_code=status)
    response.json.return_value = {"jobs": jobs}
    http = MagicMock()
    http.get.return_value = response
    return http


class BoardStatusTests(unittest.TestCase):
    def setUp(self):
        module.clear_cache()

    def test_job_gone_from_the_board_is_closed(self):
        self.assertEqual(board_status(URL, http_with([{"id": "11111111-2222-3333-4444-555555555555", "title": "Other"}])), "Closed")

    def test_job_still_listed_is_open(self):
        self.assertEqual(board_status(URL, http_with([{"id": JOB, "title": "Graphic Designer"}])), "Open")

    def test_empty_or_unreadable_board_is_unknown(self):
        self.assertEqual(board_status(URL, http_with([])), "Unknown")
        module.clear_cache()
        self.assertEqual(board_status(URL, http_with([], status=500)), "Unknown")

    def test_other_addresses_are_unknown_without_a_request(self):
        http = http_with([])
        self.assertEqual(board_status("https://www.linkedin.com/jobs/view/4474846154", http), "Unknown")
        http.get.assert_not_called()

    def test_one_board_is_read_once_for_many_jobs(self):
        http = http_with([{"id": JOB}])
        board_status(URL, http)
        board_status("https://jobs.ashbyhq.com/netgear/11111111-2222-3333-4444-555555555555", http)
        self.assertEqual(http.get.call_count, 1)


class BoardWorkplaceTests(unittest.TestCase):
    def setUp(self):
        module.clear_cache()

    def test_the_board_says_how_the_job_is_worked(self):
        job = {"id": JOB, "title": "Graphic Designer", "workplaceType": "Hybrid", "isRemote": True}
        self.assertEqual(module.board_workplace(URL, http_with([job])), "Hybrid")
        module.clear_cache()
        self.assertEqual(module.board_workplace(URL, http_with([dict(job, workplaceType="OnSite")])), "Onsite")
        module.clear_cache()
        self.assertEqual(module.board_workplace(URL, http_with([dict(job, workplaceType="Remote")])), "Remote")

    def test_no_answer_when_the_board_is_silent_or_the_job_is_gone(self):
        self.assertIsNone(module.board_workplace(URL, http_with([{"id": JOB, "title": "X"}])))
        module.clear_cache()
        self.assertIsNone(module.board_workplace(URL, http_with([{"id": "other", "workplaceType": "Hybrid"}])))
        module.clear_cache()
        self.assertIsNone(module.board_workplace("https://acme.com/jobs/1", http_with([])))

    def test_a_page_that_says_telecommute_is_overruled_by_the_board(self):
        from unittest.mock import patch
        from jobfinder.sources.job_listings import extract_jobs
        html = ('<script type="application/ld+json">{"@type": "JobPosting", "title": "Graphic Designer", '
                '"jobLocationType": "TELECOMMUTE", "description": "<p>Design things.</p>", "url": "%s", '
                '"jobLocation": {"@type": "Place", "address": {"addressLocality": "New York City", "addressRegion": "NY"}}}'
                '</script>' % URL)
        with patch.object(module, "board_workplace", return_value="Hybrid"):
            (job,) = extract_jobs(URL, html, ["Graphic Designer"])
        self.assertEqual(job["type"], "Hybrid")
        with patch.object(module, "board_workplace", return_value=None):
            (job,) = extract_jobs(URL, html, ["Graphic Designer"])
        self.assertEqual(job["type"], "Remote")  # no answer from the board: the page's data stands


if __name__ == "__main__":
    unittest.main()
