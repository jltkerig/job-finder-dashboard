import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import no_database  # noqa: F401  (cuts tests off from the real database)
from jobfinder.search import shared
from jobfinder.sources.ats_discovery import identify, identify_unreadable
from jobfinder.sources.employer_jobs import Employer

RSS = """<?xml version="1.0"?><rss version="2.0" xmlns:tt="https://teamtailor.com/locations"><channel>
<item><title>Web Designer</title><description>&lt;p&gt;Design pages&lt;/p&gt;</description><link>https://acme.teamtailor.com/jobs/77-web-designer</link>
<remoteStatus>hybrid</remoteStatus><guid>g-77</guid><tt:locations><tt:location><tt:name>Austin</tt:name><tt:city>Austin</tt:city><tt:country>United States</tt:country></tt:location></tt:locations></item>
</channel></rss>"""


class FakeHttp:
    def __init__(self, text="", data=None):
        self.text, self.data = text, data

    def get(self, url, params=None, accept="", headers=None):
        return SimpleNamespace(status_code=200, content=self.text.encode(), text=self.text, json=lambda: self.data)


class NewBoards(unittest.TestCase):
    def test_teamtailor_openings_come_from_the_job_feed(self):
        employer = Employer({"name": "Acme", "system": "teamtailor", "slug": "acme"})
        http = FakeHttp(RSS)
        [listing] = employer.adapter.search("", http)
        self.assertEqual((listing["title"], listing["location"]), ("Web Designer", "Austin, United States"))
        opening = employer.adapter.detail(listing, http, employer)
        self.assertEqual(opening["url"], "https://acme.teamtailor.com/jobs/77-web-designer")
        self.assertEqual(opening["type"], "Hybrid")
        self.assertEqual(opening["description"], "Design pages")
        self.assertEqual(employer.adapter.status("https://acme.teamtailor.com/jobs/77-web-designer", http), "Open")
        self.assertEqual(employer.adapter.status("https://acme.teamtailor.com/jobs/99-gone", http), "Closed")

    def test_recruitee_offers_come_from_the_public_json(self):
        data = {"offers": [{"id": 5, "slug": "ux-designer", "title": "UX Designer", "careers_url": "https://acme.recruitee.com/o/ux-designer",
                            "city": "Boston", "state_code": "MA", "country_code": "US", "remote": False, "description": "<p>Design</p>",
                            "requirements": "<p>Figma</p>", "published_at": "2026-09-01 10:00:00 UTC"}]}
        employer = Employer({"name": "Acme", "system": "recruitee", "slug": "acme"})
        http = FakeHttp(data=data)
        [listing] = employer.adapter.search("", http)
        self.assertEqual(listing["location"], "Boston, MA, US")
        opening = employer.adapter.detail(listing, http, employer)
        self.assertEqual((opening["title"], opening["posted"]), ("UX Designer", "2026-09-01"))
        self.assertIn("Figma", opening["description"])
        self.assertEqual(employer.adapter.status("https://acme.recruitee.com/o/ux-designer", http), "Open")
        self.assertEqual(employer.adapter.status("https://acme.recruitee.com/o/other", http), "Closed")

    def test_addresses_of_the_new_systems_are_recognised(self):
        self.assertEqual(identify("https://acme.recruitee.com/o/x"), {"system": "recruitee", "slug": "acme"})
        self.assertEqual(identify("https://acme.teamtailor.com/jobs/1"), {"system": "teamtailor", "slug": "acme"})
        for url, system in (("https://x.paradox.ai/", "paradox"), ("https://acme.recruitcrm.io/jobs", "recruitcrm"),
                            ("https://acme.fountain.com/jobs", "fountain"), ("https://acme.recruiterbox.com/", "recruiterbox"),
                            ("https://hire.trakstar.com/jobs", "recruiterbox"), ("https://cls12.bullhornstaffing.com/x", "bullhorn"),
                            ("https://acme.darwinbox.in/ms/candidate", "darwinbox"), ("https://acme.avature.net/careers", "avature"),
                            ("https://jobs.beamery.com/acme", "beamery"), ("https://acme.phenompeople.com/", "phenom"),
                            ("https://acme.breezy.hr/", "breezy"), ("https://jobs.jobvite.com/acme", "jobvite"),
                            ("https://acme.applytojob.com/apply", "jazzhr"), ("https://acme.taleo.net/careersection", "taleo"),
                            ("https://careers.acme.successfactors.com/career", "successfactors")):
            self.assertEqual((identify_unreadable(url) or {}).get("system"), system, url)

    def test_the_searches_include_icims_and_the_other_readable_systems(self):
        for site in ("icims.com", "myworkdayjobs.com", "bamboohr.com", "recruitee.com", "teamtailor.com", "apply.workable.com",
                     "jobs.smartrecruiters.com", "greenhouse.io", "jobs.lever.co", "jobs.ashbyhq.com"):
            self.assertIn(site, shared.JOB_BOARD_SITES)


if __name__ == "__main__":
    unittest.main()
