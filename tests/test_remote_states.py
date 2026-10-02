import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import no_database  # noqa: F401  (cuts tests off from the real database)
sys.path.insert(0, str(Path(__file__).resolve().parent))
import job_finder as finder
from jobfinder.search import relevance
from jobfinder.search import geo
from jobfinder.search import usa_location
from jobfinder.sources.employer_jobs import Employer
from jobfinder.sources.remote_states import is_remote_place, list_states, place_states
from test_employer_jobs import Response
from test_search_flow import FakeEmployer, employer_opening, run_search

CVS_PLACES = ['Work At Home-Massachusetts', 'Work At Home-North Carolina', 'Work At Home-Florida', 'Work At Home-Rhode Island']


class Parsing(unittest.TestCase):
    def test_work_at_home_locations_name_their_states(self):
        self.assertEqual(place_states(CVS_PLACES), {'MA', 'NC', 'FL', 'RI'})

    def test_other_remote_label_styles(self):
        self.assertEqual(place_states(['Remote - TX', 'Texas - Remote', 'Remote, Ohio', 'Remote (Georgia)', 'Remote in Oregon']),
                         {'TX', 'OH', 'GA', 'OR'})

    def test_places_that_are_not_state_limited_name_nothing(self):
        self.assertEqual(place_states(['Remote', 'Remote - US', 'Baltimore, MD', 'Portland, Oregon (hybrid)']), set())
        self.assertTrue(is_remote_place('Work At Home-Florida'))
        self.assertFalse(is_remote_place('Baltimore, MD'))

    def test_a_state_list_in_the_description(self):
        self.assertEqual(list_states('Open to candidates in the following states: CA, NY, and Texas. Other text. MD is nice.'),
                         {'CA', 'NY', 'TX'})
        self.assertEqual(list_states('We are hiring in the following states - Florida, Georgia. Apply now.'), {'FL', 'GA'})

    def test_pay_range_state_lists_are_not_restrictions(self):
        self.assertEqual(list_states('The salary range for the following states: CA, NY, WA is $90,000 to $120,000.'), set())

    def test_the_shared_restriction_function_uses_locations_and_text(self):
        self.assertEqual(usa_location.remote_state_restrictions('Great job.', None, CVS_PLACES), {'MA', 'NC', 'FL', 'RI'})


class Workday(unittest.TestCase):
    def test_posting_with_work_at_home_locations_is_remote_and_limited(self):
        info = {'jobPostingInfo': {'title': 'Senior Content Designer', 'location': CVS_PLACES[0], 'additionalLocations': CVS_PLACES[1:],
                                   'country': {'descriptor': 'United States of America'}, 'jobDescription': '<p>Write.</p>',
                                   'remoteType': 'Remote', 'externalUrl': 'https://cvs.example/job/1'}}
        http = SimpleNamespace(get=lambda url, params=None, accept=None: Response(info))
        employer = Employer({'name': 'CVS', 'system': 'workday', 'host': 'cvs.wd1.myworkdayjobs.com', 'tenant': 'cvs', 'site': 'careers'})
        opening = employer.adapter.detail({'path': '/job/1'}, http, employer)
        self.assertEqual((opening['type'], opening['remote_states']), ('Remote', {'MA', 'NC', 'FL', 'RI'}))
        self.assertEqual(employer.remote_limits('https://cvs.wd1.myworkdayjobs.com/careers/job/1', http), {'MA', 'NC', 'FL', 'RI'})

    def test_a_job_without_state_labels_has_no_limits(self):
        employer = Employer({'name': 'X', 'system': 'greenhouse', 'slug': 'x'})
        self.assertEqual(employer.remote_limits('https://boards.greenhouse.io/x/jobs/1', None), set())


class SearchAndUpdate(unittest.TestCase):
    def test_a_remote_job_open_only_in_other_states_is_skipped(self):
        skips = []
        opening = employer_opening(CVS_PLACES, 'Remote', remote_states={'MA', 'NC', 'FL', 'RI'})
        run_search({}, 'https://example.com/none', 'Web Designer', max_new=3,
                   extra={'load_employers': lambda: [FakeEmployer([opening])], 'save_company': lambda *a, **kw: True,
                          'record_skip': lambda *a: skips.append(a[0])})
        self.assertIn('Remote job limited to residents of FL, MA, NC, RI', skips)

    def test_a_remote_job_open_in_a_selected_state_is_saved_with_its_limits(self):
        captured = []
        opening = employer_opening(['Work At Home-Maryland, US'], 'Remote', remote_states={'MD', 'FL'})
        run_search({}, 'https://example.com/none', 'Web Designer', max_new=3,
                   extra={'load_employers': lambda: [FakeEmployer([opening])],
                          'save_company': lambda *a, **kw: captured.append(kw) or True})
        self.assertEqual(captured[0]['listing_details']['remote_limited_to'], ['FL', 'MD'])

    def test_an_older_saved_row_is_rechecked_against_its_workday_posting(self):
        info = {'jobPostingInfo': {'location': CVS_PLACES[0], 'additionalLocations': CVS_PLACES[1:]}}
        http = SimpleNamespace(get=lambda url, params=None, accept=None: Response(info))
        row = {'is_kept': 0, 'work_arrangement': 'Remote', 'name': 'CVS', 'source_type': 'Web',
               'source_url': 'https://cvshealth.wd1.myworkdayjobs.com/en-US/cvs_health_careers/job/Work-At-Home/Senior-Content-Designer_R1'}
        self.assertEqual(relevance.stale_remote_limit(row, {}, {'MD', 'DE'}, http), {'MA', 'NC', 'FL', 'RI'})
        self.assertIsNone(relevance.stale_remote_limit(row, {}, {'MD', 'FL'}, http))
        self.assertIsNone(relevance.stale_remote_limit(dict(row, is_kept=1), {}, {'MD'}, http))
        self.assertIsNone(relevance.stale_remote_limit(row, {'remote_limited_to': []}, {'MD'}, http))


class Places(unittest.TestCase):
    def test_zip_codes_and_counties_are_geocoded_sensibly(self):
        self.assertEqual(geo.geocode_queries('21014', 'MD, DE'), ['21014, United States'])
        self.assertEqual(geo.geocode_queries('Harford County', 'MD, DE'), ['Harford County, MD', 'Harford County, DE'])
        self.assertEqual(geo.geocode_queries('Bel Air, MD', 'MD'), ['Bel Air, MD'])

    def test_region_names_become_a_city(self):
        self.assertEqual(geo.clean_region_name('Greater Baltimore Area'), 'Baltimore')
        self.assertEqual(geo.clean_region_name('DMV'), 'Washington, DC')
        self.assertEqual(geo.clean_region_name('Bel Air'), 'Bel Air')


if __name__ == '__main__':
    unittest.main()
