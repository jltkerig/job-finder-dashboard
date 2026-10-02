import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import no_database  # noqa: F401  (cuts tests off from the real database)
import job_sites
from job_sites import Adzuna, SiteBlocked, job_site_for

ITEM = {"id": "4400112233", "title": "Graphic Designer", "company": {"display_name": "Acme Print"},
        "location": {"display_name": "Bel Air, Harford County", "area": ["US", "Maryland", "Harford County", "Bel Air"]},
        "latitude": 39.53, "longitude": -76.35, "created": "2026-09-30T08:00:00Z",
        "description": "Design <b>print</b> &amp; web layouts...", "salary_min": 52000, "salary_max": 64000.4,
        "salary_is_predicted": "1", "redirect_url": "https://www.adzuna.com/land/ad/4400112233?se=abc&utm_medium=api&utm_source=id123&v=SIGNED"}


def response(items, status=200):
    reply = MagicMock(status_code=status)
    reply.json.return_value = {"results": items}
    return reply


class AdzunaSite(unittest.TestCase):
    def setUp(self):
        env = patch.dict(os.environ, {"ADZUNA_APP_ID": "id123", "ADZUNA_APP_KEY": "key456"})
        env.start()
        self.addCleanup(env.stop)
        sleeper = patch.object(job_sites.time, "sleep")
        sleeper.start()
        self.addCleanup(sleeper.stop)

    def test_it_is_a_registered_job_site(self):
        self.assertIs(job_site_for("Adzuna"), Adzuna)

    def test_needs_both_keys(self):
        self.assertTrue(Adzuna().configured)
        with patch.dict(os.environ, {"ADZUNA_APP_ID": "", "ADZUNA_APP_KEY": "key456"}):
            self.assertFalse(Adzuna().configured)
        with patch.dict(os.environ, {"ADZUNA_APP_ID": "id123", "ADZUNA_APP_KEY": ""}):
            self.assertFalse(Adzuna().configured)

    def test_search_sends_keys_kilometres_and_reads_the_listing(self):
        with patch.object(job_sites.requests, "get", return_value=response([ITEM])) as get:
            [job] = Adzuna().search("graphic designer", "Bel Air, MD", 5)
        params = get.call_args.kwargs["params"]
        self.assertEqual((params["app_id"], params["app_key"]), ("id123", "key456"))
        self.assertEqual(params["where"], "Bel Air, Maryland")
        self.assertEqual(params["distance"], 8)  # 5 miles in kilometres
        self.assertEqual(get.call_count, 1)  # one page: the free limit is 250 requests a day
        self.assertEqual(job["title"], "Graphic Designer")
        self.assertEqual(job["company"], "Acme Print")
        self.assertEqual((job["city"], job["state"]), ("Bel Air", "Maryland"))
        self.assertEqual(job["location"], "Bel Air, MD")  # a city and state, so the U.S. and distance checks can read it
        self.assertEqual((job["lat"], job["lon"]), (39.53, -76.35))
        self.assertEqual(job["posted"], "2026-09-30")
        self.assertEqual(job["url"], "https://www.adzuna.com/land/ad/4400112233?utm_medium=api&utm_source=id123&v=SIGNED")  # no per-search "se" code
        self.assertIn("Design print & web layouts...", job["description"])
        self.assertIn("Pay: $52,000 - $64,000 per year (estimated by Adzuna)", job["description"])
        self.assertIn("Jobs by Adzuna", job["description"])  # the attribution its terms ask for

    def test_a_state_sends_no_distance_and_remote_reads_as_u_s_remote(self):
        remote = {"id": "9", "title": "Remote Web Designer", "redirect_url": "https://www.adzuna.com/land/ad/9",
                  "location": {"display_name": "US", "area": ["US"]}}
        with patch.object(job_sites.requests, "get", return_value=response([remote])) as get:
            [job] = Adzuna().search("web designer", "Maryland", None)
        self.assertNotIn("distance", get.call_args.kwargs["params"])
        self.assertEqual(job["location"], "United States (Remote)")

    def test_listings_without_an_id_or_link_are_dropped(self):
        with patch.object(job_sites.requests, "get", return_value=response([{"title": "No id"}, ITEM])):
            self.assertEqual(len(Adzuna().search("designer")), 1)

    def test_bad_keys_give_a_clear_message_and_the_limit_backs_off(self):
        with patch.object(job_sites.requests, "get", return_value=response([], status=401)):
            with self.assertRaisesRegex(ValueError, "refused the keys"):
                Adzuna().search("designer")
        with patch.object(job_sites.requests, "get", return_value=response([], status=429)):
            with self.assertRaises(SiteBlocked):
                Adzuna().search("designer")

    def test_the_same_ad_gives_the_same_link_every_search(self):
        again = dict(ITEM, redirect_url="https://www.adzuna.com/land/ad/4400112233?se=DIFFERENT&utm_medium=api&utm_source=id123&v=SIGNED")
        with patch.object(job_sites.requests, "get", side_effect=[response([ITEM]), response([again])]):
            [first] = Adzuna().search("graphic designer", "Bel Air, MD", 5)
            [second] = Adzuna().search("web designer", "Baltimore, MD", 5)
        self.assertEqual(first["url"], second["url"])  # or the job would be saved again as new each search

    def test_status_never_guesses_closed(self):
        self.assertEqual(Adzuna().status("https://www.adzuna.com/land/ad/1"), "Unknown")


if __name__ == "__main__":
    unittest.main()
