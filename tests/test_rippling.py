import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import no_database  # noqa: F401  (cuts tests off from the real database)
from jobfinder.sources import board_status as board
from jobfinder.sources.ats_discovery import identify
from jobfinder.sources.employer_jobs import ADAPTERS, Employer

UUID = "cf18920c-b941-4f22-8097-4bafe822e8bc"
LISTED = [
    {"uuid": UUID, "name": " Product Design Lead ", "department": {"label": "Design"},
     "url": f"https://ats.rippling.com/acme/jobs/{UUID}", "workLocation": {"label": "New York, NY"}},
    {"uuid": "11111111-2222-3333-4444-555555555555", "name": "Accountant", "url": "https://ats.rippling.com/acme/jobs/x",
     "workLocation": {"label": "Remote (Dallas, Texas, US)"}},
]
DETAIL = {"uuid": UUID, "name": "Product Design Lead", "companyName": "Acme",
          "description": {"role": "<p>Design the product.</p>", "company": "<p>About Acme.</p>"},
          "workLocations": ["New York, NY"], "employmentType": {"label": "SALARIED_FT"},
          "createdOn": "2026-10-06T10:35:53.811000-07:00"}


def fake_http(board_jobs=LISTED, detail=DETAIL):
    def get(url, params=None, **kwargs):
        response = MagicMock(status_code=200)
        is_detail = "/jobs/" in url and not url.rstrip("/").endswith("/jobs")
        response.json.return_value = detail if is_detail else board_jobs
        return response
    http = MagicMock()
    http.get.side_effect = get
    return http


class RipplingTests(unittest.TestCase):
    def setUp(self):
        board.clear_cache()

    def test_rippling_addresses_are_recognised(self):
        self.assertEqual(identify(f"https://ats.rippling.com/acme/jobs/{UUID}"), {"system": "rippling", "slug": "acme"})
        self.assertEqual(identify("https://ats.rippling.com/acme-careers/jobs"), {"system": "rippling", "slug": "acme-careers"})
        self.assertEqual(identify("https://ats.rippling.com/en-GB/acme/jobs"), {"system": "rippling", "slug": "acme"})
        self.assertIsNone(identify("https://ats.rippling.com/"))
        self.assertIsNone(identify("https://www.rippling.com/careers"))  # Rippling's own marketing site is not a board

    def test_the_board_is_searched_and_its_jobs_are_read(self):
        self.assertIn("rippling", ADAPTERS)
        employer = Employer({"name": "Acme", "system": "rippling", "slug": "acme"})
        (opening,) = employer.find_openings(["Product Design Lead"], fake_http())
        self.assertEqual(opening["title"], "Product Design Lead")
        self.assertEqual(opening["url"], f"https://ats.rippling.com/acme/jobs/{UUID}")
        self.assertEqual(opening["locations"], ["New York, NY"])
        self.assertEqual(opening["schedule"], "FULL_TIME")
        self.assertEqual(opening["posted"], "2026-10-06")
        self.assertIn("Design the product.", opening["description"])
        self.assertEqual(employer.domain_for(opening["url"]), "ats.rippling.com")

    def test_a_remote_place_makes_the_job_remote(self):
        employer = Employer({"name": "Acme", "system": "rippling", "slug": "acme"})
        (opening,) = employer.find_openings(["Accountant"], fake_http(detail={}))
        self.assertEqual(opening["type"], "Remote")

    def test_open_or_closed_comes_from_the_boards_list(self):
        url = f"https://ats.rippling.com/acme/jobs/{UUID}"
        self.assertEqual(board.board_status(url, fake_http()), "Open")
        board.clear_cache()
        self.assertEqual(board.board_status(url, fake_http(board_jobs=[LISTED[1]])), "Closed")


if __name__ == "__main__":
    unittest.main()
