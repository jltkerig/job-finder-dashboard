import unittest

from jobfinder.sources import local_government as lg


class Reply:
    def __init__(self, url, text="", status=200, data=None):
        self.url, self.text, self.status_code, self._data = url, text, status, data

    def json(self):
        return self._data


CENSUS = {"result": {"geographies": {"States": [{"NAME": "Maryland", "STUSAB": "MD"}],
                                     "Counties": [{"BASENAME": "Baltimore", "NAME": "Baltimore County"}],
                                     "Incorporated Places": []}}}


def fake_get(url, **kwargs):
    if "census" in url:
        return Reply(url, data=CENSUS)
    slug = url.rsplit("/", 1)[-1]
    if slug == "baltimorecounty":
        return Reply(url, "<title>Job Opportunities | Career Opportunities at Baltimore County</title>")
    if slug == "maryland":
        return Reply(url, "<title>Job Opportunities | State of Maryland Careers</title>")
    return Reply("https://www.governmentjobs.com/", "<title>GovernmentJobs | City, State</title>")  # an unknown agency goes home


class LocalGovernmentTests(unittest.TestCase):
    def setUp(self):
        lg.clear_cache()

    def test_candidates_cover_county_town_and_state(self):
        area = {"state": "Maryland", "state_code": "MD", "county": "Harford", "town": "Bel Air"}
        kinds = dict(lg.candidates(area))
        self.assertIn("harfordcounty", kinds["County"])
        self.assertIn("harfordcountymd", kinds["County"])
        self.assertIn("cityofbelair", kinds["Town"])
        self.assertIn("maryland", kinds["State"])

    def test_only_agencies_with_a_job_page_are_suggested(self):
        result = lg.suggestions((39.4, -76.6), get=fake_get)
        self.assertEqual([(f["kind"], f["name"], f["agency"]) for f in result["found"]],
                         [("County", "Baltimore County", "baltimorecounty"), ("State", "State of Maryland", "maryland")])

    def test_a_failed_county_lookup_is_none(self):
        def broken(url, **kwargs):
            raise lg.requests.RequestException("down")
        self.assertIsNone(lg.suggestions((39.4, -76.6), get=broken))


if __name__ == "__main__":
    unittest.main()
