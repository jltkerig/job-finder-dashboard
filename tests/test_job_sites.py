import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import no_database  # noqa: F401  (cuts tests off from the real database)
sys.path.insert(0, str(Path(__file__).resolve().parent))
import job_sites
from job_sites import NationalLaborExchange, SiteBlocked, search_places
from test_search_flow import run_search

GUID = "2A10561554C74DC4AA13F030D7B3494C"


def nlx_job(guid=GUID, title="Graphic Designer / Multimedia Specialist", company="Three Saints Bay",
            city="Aberdeen", state="MD"):
    return {"guid": guid, "title_exact": title, "company_exact": company, "city_exact": city, "state_short_exact": state,
            "location_exact": f"{city}, {state}", "country_exact": "United States", "GeoLocation": "39.5, -76.16",
            "miles": 7.7, "date_new": "2026-09-28T10:00:00Z", "description": "Design web pages &amp; graphics..."}


class Reply:
    def __init__(self, data, status=200):
        self.data, self.status_code = data, status

    def json(self):
        return self.data


class NationalLaborExchangeReader(unittest.TestCase):
    def setUp(self):
        self.calls = []
        patcher = patch.object(NationalLaborExchange, "DELAY", 0)
        patcher.start()
        self.addCleanup(patcher.stop)

    def fake_get(self, pages):
        def get(url, params=None, timeout=None, headers=None):
            self.calls.append((params, headers))
            return pages.pop(0)
        return patch.object(job_sites.requests, "get", side_effect=get)

    def test_a_search_reads_two_pages_at_most_and_says_who_it_is(self):
        more = {"jobs": [nlx_job()], "pagination": {"has_more_pages": True}}
        with self.fake_get([Reply(more), Reply(more), Reply(more)]):
            listings = NationalLaborExchange().search("web designer", "Bel Air, MD", 20)
        self.assertEqual(len(self.calls), 2)
        params, headers = self.calls[0]
        self.assertEqual(params, {"q": "web designer", "page": 1, "location": "Bel Air, MD", "r": 20})
        self.assertIn("PersonalJobFinder", headers["User-Agent"])
        self.assertEqual(headers["x-origin"], "usnlx.com")
        job = listings[0]
        self.assertEqual(job["url"], f"https://usnlx.com/{GUID}/job/")
        self.assertEqual(job["company"], "Three Saints Bay")
        self.assertEqual(job["location"], "Aberdeen, MD")
        self.assertEqual((job["lat"], job["lon"]), (39.5, -76.16))
        self.assertEqual(job["posted"], "2026-09-28")
        self.assertEqual(job["description"], "Design web pages & graphics...")

    def test_the_last_page_ends_the_search(self):
        with self.fake_get([Reply({"jobs": [nlx_job()], "pagination": {"has_more_pages": False}})]):
            NationalLaborExchange().search("web designer")
        self.assertEqual(len(self.calls), 1)
        self.assertNotIn("location", self.calls[0][0])

    def test_being_told_to_slow_down_stops_the_site(self):
        with self.fake_get([Reply({}, status=429)]):
            with self.assertRaises(SiteBlocked):
                NationalLaborExchange().search("web designer")

    def test_refresh_asks_whether_the_job_is_still_listed(self):
        url = f"https://usnlx.com/{GUID}/job/"
        with self.fake_get([Reply({"pagination": {"total": 1}}), Reply({"pagination": {"total": 0}}), Reply({}, status=500)]):
            self.assertEqual(NationalLaborExchange().status(url), "Open")
            self.assertEqual(NationalLaborExchange().status(url), "Closed")
            self.assertEqual(NationalLaborExchange().status(url), "Unknown")
        self.assertEqual(self.calls[0][0], {"q": f"guid:{GUID}"})
        self.assertEqual(NationalLaborExchange().status("https://usnlx.com/about"), "Unknown")

    def test_places_are_your_cities_with_their_radius_or_whole_states(self):
        cities = [{"city": "Bel Air, MD", "radius": 20}, {"city": "Delaware", "radius": 50}, {"city": "Bel Air, MD", "radius": 20}]
        self.assertEqual(search_places("MD", cities, {"delaware": "DE"}), [("Bel Air, MD", 20), ("Delaware", None)])
        self.assertEqual(search_places("MD, DE", [], {}), [("MD", None), ("DE", None)])


class FakeSite:
    name = "National Labor Exchange"
    domain = "usnlx.com"
    requests = 1

    def __init__(self):
        self.searches = []

    def search(self, keyword, place="", radius=None):
        self.searches.append((keyword, place, radius))
        return [
            NationalLaborExchange()._listing(nlx_job()),
            NationalLaborExchange()._listing(nlx_job(guid="B" * 32, title="Forklift Operator", company="Acme Logistics")),
            NationalLaborExchange()._listing(nlx_job(guid="C" * 32, title="Web Designer", company="Far Away Inc",
                                                     city="Seattle", state="WA")),
        ]


class JobSiteSearch(unittest.TestCase):
    def test_matching_nearby_openings_are_saved_with_their_employer(self):
        records = []
        distance = lambda database, html, fallback, state, targets, allow_footer=False: (
            ("Seattle" not in fallback), fallback.split(",")[0], 39.5, -76.16, 7.7)
        saved = run_search({}, "https://example.com/none", "Web Designer, Graphic Designer", records=records,
                           cities_json='[{"city": "Bel Air, MD", "radius": 20}]',
                           city_targets=[{"city": "Bel Air, MD", "radius": 20, "lat": 39.53, "lon": -76.35}],
                           distance=distance, extra={"JOB_SITES": (FakeSite,)})
        self.assertEqual(saved, {f"https://usnlx.com/{GUID}/job/"})  # forklift: wrong title; Seattle: too far
        args = records[0]
        self.assertEqual(args[1], "Three Saints Bay")  # the employer, not the job site
        self.assertEqual(args[4], "usnlx.com")

    def test_the_site_can_be_switched_off(self):
        searched = []

        class Watched(FakeSite):
            def search(self, *args):
                searched.append(args)
                return []

        run_search({}, "https://example.com/none", "Web Designer", extra={"JOB_SITES": (Watched,)})
        self.assertTrue(searched)  # on by default
        searched.clear()
        with patch("job_finder.settings", {"job_sites": {"National Labor Exchange": False}}):
            run_search({}, "https://example.com/none", "Web Designer", extra={"JOB_SITES": (Watched,)})
        self.assertEqual(searched, [])


    def test_each_job_site_gets_its_own_share_of_the_search(self):
        """The first site filling its share (a third of the search) must not leave the next site with nothing."""
        class SecondSite(FakeSite):
            name = "Adzuna"
            domain = "adzuna.com"

            def search(self, keyword, place="", radius=None):
                self.searches.append((keyword, place, radius))
                return [NationalLaborExchange()._listing(nlx_job(guid="D" * 32, title="Web Designer", company="Second Co"))]

        saved = run_search({}, "https://example.com/none", "Web Designer", max_new=3,
                           cities_json='[{"city": "Bel Air, MD", "radius": 20}]',
                           city_targets=[{"city": "Bel Air, MD", "radius": 20, "lat": 39.53, "lon": -76.35}],
                           distance=lambda database, html, fallback, state, targets, allow_footer=False: (
                               ("Seattle" not in fallback), fallback.split(",")[0], 39.5, -76.16, 7.7),
                           extra={"JOB_SITES": (FakeSite, SecondSite)})
        self.assertEqual(saved, {f"https://usnlx.com/{GUID}/job/", f"https://usnlx.com/{'D' * 32}/job/"})

if __name__ == "__main__":
    unittest.main()
