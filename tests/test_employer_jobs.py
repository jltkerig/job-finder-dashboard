import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import employer_jobs
from employer_jobs import DEFAULT_EMPLOYERS, Employer, employer_for_url, load_employers

HOME_DEPOT = next(config for config in DEFAULT_EMPLOYERS if config["name"] == "The Home Depot")
JPMORGAN = next(config for config in DEFAULT_EMPLOYERS if config["name"] == "JPMorgan Chase")


class Response:
    def __init__(self, payload=None, status=200, text=''):
        self.payload, self.status_code, self.text = payload, status, text

    def json(self):
        return self.payload


class FakeHttp:
    """Answers Workday/Oracle requests from a dict: search text -> listings, path -> detail."""

    def __init__(self, searches=None, details=None, oracle=None, status=200, pages=None):
        self.searches, self.details, self.oracle, self.status = searches or {}, details or {}, oracle or {}, status
        self.pages = pages or {}
        self.calls = []

    def post_json(self, url, payload):
        self.calls.append(("post", payload["searchText"], payload["offset"]))
        if self.status != 200:
            return Response(status=self.status)
        posts = self.searches.get(payload["searchText"], [])
        page = posts[payload["offset"]: payload["offset"] + payload["limit"]]
        return Response({"total": len(posts), "jobPostings": page})

    def get(self, url, params=None, accept=None):
        self.calls.append(("get", url))
        if url in self.pages:
            page = self.pages[url]
            return page if isinstance(page, Response) else Response(text=page)
        if "recruitingCEJobRequisitions" in url:
            keyword = params["finder"].split("keyword=")[1].split(",sortBy")[0]
            return Response({"items": [{"requisitionList": self.oracle.get(keyword, [])}]})
        if "recruitingCEJobRequisitionDetails" in url:
            body = self.oracle.get("detail")
            return Response({"items": [body] if body else []}, 200 if body is not None else 404)
        path = "/job/" + url.split("/job/", 1)[1] if "/job/" in url else url
        info = self.details.get(path)
        return Response({"jobPostingInfo": info} if info else {}, 200 if info else 404)


def posting(title, location, path):
    return {"title": title, "externalPath": path, "locationsText": location}


def detail(title, location, **extra):
    info = {"title": title, "location": location, "startDate": "2026-09-20", "timeType": "Full time",
            "country": {"descriptor": "United States of America"}, "jobDescription": "<p>Build things in Figma.</p>",
            "externalUrl": f"https://homedepot.wd5.myworkdayjobs.com/CareerDepot/job/x/{title.replace(' ', '-')}_1"}
    info.update(extra)
    return info


class WorkdaySearch(unittest.TestCase):
    def setUp(self):
        self.employer = Employer(HOME_DEPOT)

    def test_results_are_paged_until_the_total_is_reached(self):
        posts = [posting(f'Web Designer {n}', 'ATLANTA', f'/job/a/{n}') for n in range(45)]
        http = FakeHttp(searches={'web designer': posts})
        self.assertEqual(len(self.employer.adapter.search('web designer', http)), 45)
        self.assertEqual([call[2] for call in http.calls], [0, 20, 40])

    def test_only_title_matches_that_pass_the_employers_filters_are_opened(self):
        posts = [posting('Senior UX Designer', 'STORE SUPPORT CENTER, ATLANTA - 9090', '/job/a/ux'),
                 posting('Web Designer', 'MARYLAND - VIRTUAL - MD01', '/job/b/web'),
                 posting('Kitchen Designer', 'STORE 0121', '/job/c/kitchen'),
                 posting('Web Designer Supervisor', 'NEWARK DFC - 5854', '/job/d/dfc')]
        details = {'/job/a/ux': detail('Senior UX Designer', 'STORE SUPPORT CENTER, ATLANTA - 9090', remoteType='Onsite'),
                   '/job/b/web': detail('Web Designer', 'MARYLAND - VIRTUAL - MD01')}
        http = FakeHttp(searches={'web designer': posts, 'ux designer': posts}, details=details)
        found = self.employer.find_openings(['Web Designer'], http)
        self.assertEqual(sorted(o['title'] for o in found), ['Senior UX Designer', 'Web Designer'])

    def test_a_virtual_job_is_remote_and_limited_to_its_state(self):
        posts = [posting('Web Designer', 'MARYLAND - VIRTUAL - MD01', '/job/b/web')]
        http = FakeHttp(searches={'web designer': posts},
                        details={'/job/b/web': detail('Web Designer', 'MARYLAND - VIRTUAL - MD01')})
        opening = self.employer.find_openings(['Web Designer'], http)[0]
        self.assertEqual((opening['type'], opening['remote_states']), ('Remote', {'MD'}))
        self.assertEqual(opening['location'], 'MARYLAND - VIRTUAL - MD01, US')
        self.assertEqual((opening['schedule'], opening['posted'], opening['country']),
                         ('FULL_TIME', '2026-09-20', 'United States of America'))

    def test_excluded_places_are_dropped_from_multi_location_jobs(self):
        posts = [posting('Web Designer', '2 Locations', '/job/b/web')]
        http = FakeHttp(searches={'web designer': posts}, details={
            '/job/b/web': detail('Web Designer', 'NEWARK DFC - 5854', additionalLocations=['Baltimore, MD'])})
        opening = self.employer.find_openings(['Web Designer'], http)[0]
        self.assertEqual(opening['locations'], ['Baltimore, MD, US'])

    def test_a_job_whose_places_are_all_excluded_is_dropped(self):
        posts = [posting('Web Designer', '2 Locations', '/job/b/web')]
        http = FakeHttp(searches={'web designer': posts}, details={'/job/b/web': detail('Web Designer', 'NEWARK DFC - 5854')})
        self.assertEqual(self.employer.find_openings(['Web Designer'], http), [])

    def test_other_countries_are_labelled_so_they_can_be_screened_out(self):
        config = dict(HOME_DEPOT, title_keywords=[], exclude_locations=[])
        posts = [posting('Web Designer', 'Paris', '/job/p/web')]
        http = FakeHttp(searches={'web designer': posts}, details={
            '/job/p/web': detail('Web Designer', 'Paris', country={'descriptor': 'France'})})
        opening = Employer(config).find_openings(['Web Designer'], http)[0]
        self.assertEqual((opening['location'], opening['country']), ('Paris, France', 'France'))

    def test_a_workday_error_is_reported_not_swallowed(self):
        with self.assertRaises(ValueError):
            self.employer.find_openings(['Web Designer'], FakeHttp(status=500))


class WorkdayStatus(unittest.TestCase):
    URL = 'https://homedepot.wd5.myworkdayjobs.com/en-US/CareerDepot/job/a/Web-Designer_1'

    def status(self, info, code=200):
        employer = Employer(HOME_DEPOT)
        http = FakeHttp(details={'/job/a/Web-Designer_1': info} if info else {})
        if code != 200:
            http.get = lambda url, params=None: Response(status=code)
        return employer.status(self.URL, http)

    def test_open_closed_and_unknown(self):
        self.assertEqual(self.status({'title': 'x', 'posted': True}), 'Open')
        self.assertEqual(self.status(None), 'Closed')
        self.assertEqual(self.status({'title': 'x', 'posted': False}), 'Closed')
        self.assertEqual(self.status({'title': 'x', 'posted': True, 'endDate': '2020-01-01'}), 'Closed')
        self.assertEqual(self.status({'title': 'x'}, code=503), 'Unknown')


class OracleSearch(unittest.TestCase):
    def setUp(self):
        self.employer = Employer(JPMORGAN)
        self.listing = {'Id': 111, 'Title': 'Product Designer', 'PrimaryLocation': 'New York, NY, United States',
                        'PostedDate': '2026-09-29', 'WorkplaceType': 'Hybrid', 'JobFamily': 'Design',
                        'ShortDescriptionStr': 'Short.',
                        'secondaryLocations': [{'Name': 'Wilmington, DE, United States', 'CountryCode': 'US'}]}

    def test_a_listing_becomes_an_opening_with_every_location_and_the_full_description(self):
        http = FakeHttp(oracle={'product designer': [self.listing],
                                'detail': {'ExternalDescriptionStr': '<p>Full text.</p>', 'ExternalResponsibilitiesStr': '<p>Do.</p>'}})
        opening = self.employer.find_openings(['Product Designer'], http)[0]
        self.assertEqual(opening['locations'], ['New York, NY, United States', 'Wilmington, DE, United States'])
        self.assertEqual((opening['type'], opening['posted'], opening['category']), ('Hybrid', '2026-09-29', 'Design'))
        self.assertIn('Full text.', opening['description'])
        self.assertEqual(opening['url'], 'https://jpmc.fa.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1001/job/111')

    def test_the_short_description_is_used_when_the_full_one_is_unavailable(self):
        http = FakeHttp(oracle={'product designer': [self.listing]})
        self.assertEqual(self.employer.find_openings(['Product Designer'], http)[0]['description'], 'Short.')

    def test_status(self):
        url = 'https://jpmc.fa.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1001/job/111'
        self.assertEqual(self.employer.status(url, FakeHttp(oracle={'detail': {'Id': 111}})), 'Open')
        self.assertEqual(self.employer.status(url, FakeHttp(oracle={})), 'Closed')


ALLEGIS = next(config for config in DEFAULT_EMPLOYERS if config["name"] == "Allegis Group")
UNDER_ARMOUR = next(config for config in DEFAULT_EMPLOYERS if config["name"] == "Under Armour")

ICIMS_LIST = ('<div class="iCIMS_JobsTable"><div class="row"><a href="https://careers-allegisgroup.icims.com/jobs/2368/ux-designer/job?in_iframe=1">'
              '<span class="sr-only">Title</span><h3>UX Designer</h3></a></div>'
              '<div class="row"><a href="https://careers-allegisgroup.icims.com/jobs/9/truck-driver/job?in_iframe=1"><h3>Truck Driver</h3></a></div></div>')


def icims_page(title='UX Designer'):
    record = {"@type": "JobPosting", "title": title, "datePosted": "2026-09-20", "employmentType": "FULL_TIME",
              "hiringOrganization": {"name": "Allegis Group"}, "description": "<p>Design things.</p>",
              "jobLocation": {"address": {"addressLocality": "Hanover", "addressRegion": "MD", "addressCountry": "US"}}}
    return '<script type="application/ld+json">' + json.dumps(record) + '</script>'


class ICIMSSearch(unittest.TestCase):
    URL = 'https://careers-allegisgroup.icims.com/jobs/2368/ux-designer/job'

    def test_the_list_gives_titles_and_each_job_page_gives_the_details(self):
        http = FakeHttp(pages={'https://careers-allegisgroup.icims.com/jobs/search': ICIMS_LIST,
                               self.URL + '?in_iframe=1': icims_page()})
        opening = Employer(ALLEGIS).find_openings(['UX Designer'], http)[0]
        self.assertEqual((opening['title'], opening['location'], opening['posted'], opening['schedule']),
                         ('UX Designer', 'Hanover, MD, US', '2026-09-20', 'FULL_TIME'))
        self.assertEqual(opening['url'], self.URL)  # the public page, not the embedded view

    def test_a_job_page_without_a_job_record_means_closed(self):
        employer = Employer(ALLEGIS)
        self.assertEqual(employer.status(self.URL, FakeHttp(pages={self.URL: icims_page()})), 'Open')
        self.assertEqual(employer.status(self.URL, FakeHttp(pages={self.URL: '<p>This job is no longer available.</p>'})), 'Closed')
        self.assertEqual(employer.status(self.URL, FakeHttp(pages={self.URL: Response(status=404)})), 'Closed')


SF_LIST = ('<a class="jobTitle-link" href="/job/Baltimore-Lead-Digital-Designer/1001/">Lead, Digital Designer</a>'
           '<a class="jobTitle-link" href="/job/Baltimore-Lead-Digital-Designer/1001/">Lead, Digital Designer</a>'
           '<a class="jobTitle-link" href="/job/Baltimore-Cashier/1002/">Cashier</a>')
SF_JOB = ('<span itemprop="title">Lead, Digital Designer</span><meta itemprop="datePosted" content="Wed Sep 02 07:00:00 UTC 2026">'
          '<span itemprop="jobLocation"><span itemprop="addressLocality">Baltimore</span><span itemprop="addressRegion">MD</span>'
          '<span itemprop="addressCountry">US</span></span><span itemprop="description"><p>Design apparel pages.</p></span>')


class SuccessFactorsSearch(unittest.TestCase):
    JOB = 'https://careers.underarmour.com/job/Baltimore-Lead-Digital-Designer/1001/'

    def test_titles_come_from_the_list_and_places_from_the_job_page(self):
        http = FakeHttp(pages={'https://careers.underarmour.com/search/': SF_LIST, self.JOB: SF_JOB})
        openings = Employer(UNDER_ARMOUR).find_openings(['Digital Designer'], http)
        self.assertEqual(len(openings), 1)
        opening = openings[0]
        self.assertEqual((opening['title'], opening['location'], opening['posted'], opening['url']),
                         ('Lead, Digital Designer', 'Baltimore, MD, US', '2026-09-02', self.JOB))
        self.assertIn('Design apparel pages.', opening['description'])

    def test_status(self):
        employer = Employer(UNDER_ARMOUR)
        self.assertEqual(employer.status(self.JOB, FakeHttp(pages={self.JOB: SF_JOB})), 'Open')
        self.assertEqual(employer.status(self.JOB, FakeHttp(pages={self.JOB: '<p>Job not found</p>'})), 'Closed')


class EmployerList(unittest.TestCase):
    def test_the_defaults_are_used_when_there_is_no_file(self):
        names = [employer.name for employer in load_employers(Path('does-not-exist.json'))]
        self.assertIn('The Home Depot', names)
        self.assertEqual(len(names), len(DEFAULT_EMPLOYERS))

    def test_a_users_file_replaces_the_defaults_and_skips_disabled_or_broken_entries(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'watched_employers.json'
            path.write_text(json.dumps([
                dict(HOME_DEPOT), dict(HOME_DEPOT, name='Off', enabled=False), {'name': 'Broken'},
                dict(HOME_DEPOT, name='Unknown system', system='taleo')]), encoding='utf-8')
            self.assertEqual([e.name for e in load_employers(path)], ['The Home Depot'])

    def test_the_shipped_file_matches_the_defaults(self):
        shipped = json.loads((Path(employer_jobs.__file__).parent / 'watched_employers.json').read_text(encoding='utf-8'))
        self.assertEqual(shipped, DEFAULT_EMPLOYERS)

    def test_a_posting_url_finds_its_employer(self):
        employers = load_employers(Path('does-not-exist.json'))
        self.assertEqual(employer_for_url('https://troweprice.wd5.myworkdayjobs.com/TRowePrice/job/x/y_1', employers).name, 'T. Rowe Price')
        self.assertIsNone(employer_for_url('https://example.com/job/1', employers))


if __name__ == '__main__':
    unittest.main()
