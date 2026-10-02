import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import no_database  # noqa: F401  (cuts tests off from the real database)
from jobfinder.sources import job_sites
from jobfinder.sources.job_sites import SiteBlocked, USAJobs, job_site_for

ITEM = {"MatchedObjectId": "810000001", "MatchedObjectDescriptor": {
    "PositionID": "DE-12345678-24-ABC", "PositionTitle": "Visual Information Specialist (Graphic Design)",
    "OrganizationName": "Army Garrison Aberdeen Proving Ground", "DepartmentName": "Department of the Army",
    "PositionLocation": [{"LocationName": "Aberdeen Proving Ground, Maryland", "CountryCode": "United States",
                          "CountrySubDivisionCode": "Maryland", "CityName": "Aberdeen Proving Ground, Maryland",
                          "Longitude": -76.1, "Latitude": 39.4}],
    "QualificationSummary": "One year of specialized experience in graphic design.",
    "PositionRemuneration": [{"MinimumRange": "70000", "MaximumRange": "90000", "Description": "Per Year"}],
    "PublicationStartDate": "2026-09-30T00:00:00.000",
    "UserArea": {"Details": {"JobSummary": "Designs print &amp; web materials.", "MajorDuties": ["Create layouts", "Edit photos"]}}}}


def response(items, status=200):
    reply = MagicMock(status_code=status)
    reply.json.return_value = {"SearchResult": {"SearchResultItems": items}}
    return reply


class USAJobsSite(unittest.TestCase):
    def setUp(self):
        env = patch.dict(os.environ, {"USAJOBS_API_KEY": "key123", "USAJOBS_EMAIL": "me@example.com"})
        env.start()
        self.addCleanup(env.stop)
        sleeper = patch.object(job_sites.time, "sleep")
        sleeper.start()
        self.addCleanup(sleeper.stop)

    def test_it_is_a_registered_job_site(self):
        self.assertIs(job_site_for("USAJOBS"), USAJobs)

    def test_needs_both_key_and_email(self):
        self.assertTrue(USAJobs().configured)
        with patch.dict(os.environ, {"USAJOBS_API_KEY": "", "USAJOBS_EMAIL": "me@example.com"}):
            self.assertFalse(USAJobs().configured)
        with patch.dict(os.environ, {"USAJOBS_API_KEY": "key123", "USAJOBS_EMAIL": ""}):
            self.assertFalse(USAJobs().configured)

    def test_search_sends_key_email_and_spelled_out_state_then_reads_the_listing(self):
        with patch.object(job_sites.requests, "get", return_value=response([ITEM])) as get:
            listings = USAJobs().search("graphic design", "Bel Air, MD", 20)
        _, kwargs = get.call_args
        self.assertEqual(kwargs["headers"], {"Host": "data.usajobs.gov", "User-Agent": "me@example.com",
                                             "Authorization-Key": "key123"})
        self.assertEqual(kwargs["params"]["LocationName"], "Bel Air, Maryland")
        self.assertEqual(kwargs["params"]["Radius"], 20)
        self.assertEqual(kwargs["params"]["WhoMayApply"], "public")
        [job] = listings
        self.assertEqual(job["title"], "Visual Information Specialist (Graphic Design)")
        self.assertEqual(job["company"], "Army Garrison Aberdeen Proving Ground")
        self.assertEqual(job["url"], "https://www.usajobs.gov/job/DE-12345678-24-ABC")
        self.assertEqual((job["location"], job["city"], job["state"]), ("Aberdeen Proving Ground, Maryland",
                                                                       "Aberdeen Proving Ground", "Maryland"))
        self.assertEqual((job["lat"], job["lon"]), (39.4, -76.1))
        self.assertEqual(job["posted"], "2026-09-30")
        self.assertIn("Designs print & web materials.", job["description"])  # HTML entity decoded
        self.assertIn("Create layouts\nEdit photos", job["description"])
        self.assertIn("Pay: $70000 - $90000 per Per Year", job["description"])

    def test_a_whole_state_sends_no_radius_and_remote_jobs_read_as_u_s_remote(self):
        remote = {"MatchedObjectDescriptor": {"PositionID": "R1", "PositionTitle": "Web Designer",
                  "PositionLocation": [{"LocationName": "Anywhere in the U.S. (remote job)"}]}}
        with patch.object(job_sites.requests, "get", return_value=response([remote])) as get:
            [job] = USAJobs().search("web designer", "Maryland", None)
        self.assertNotIn("Radius", get.call_args.kwargs["params"])
        self.assertEqual(job["location"], "United States (Remote)")
        self.assertEqual(job["country"], "United States")

    def test_a_full_page_asks_for_the_next_one_and_a_short_page_stops(self):
        full = [{"MatchedObjectDescriptor": {"PositionID": f"J{i}", "PositionTitle": "Web Designer"}} for i in range(25)]
        with patch.object(job_sites.requests, "get", side_effect=[response(full), response([ITEM])]) as get:
            self.assertEqual(len(USAJobs().search("web designer")), 26)
        self.assertEqual(get.call_count, 2)

    def test_bad_key_gives_a_clear_message_and_rate_limit_backs_off(self):
        with patch.object(job_sites.requests, "get", return_value=response([], status=401)):
            with self.assertRaisesRegex(ValueError, "refused the API key"):
                USAJobs().search("web designer")
        with patch.object(job_sites.requests, "get", return_value=response([], status=429)):
            with self.assertRaises(SiteBlocked):
                USAJobs().search("web designer")

    def test_status_never_guesses_closed(self):
        self.assertEqual(USAJobs().status("https://www.usajobs.gov/job/810000001"), "Unknown")


if __name__ == "__main__":
    unittest.main()
