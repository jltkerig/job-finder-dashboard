import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import no_database  # noqa: F401  (cuts tests off from the real database)
sys.path.insert(0, str(Path(__file__).resolve().parent))
from jobfinder.sources.ats_discovery import identify, identify_unreadable, pretty_name
from jobfinder.sources.employer_jobs import Employer, config_key, load_employers, save_discovered
from test_employer_jobs import Response
from test_search_flow import FakeEmployer, employer_opening, run_search

GUID = 'b181f77f-0432-453f-b229-869d786bb46c'


class Identify(unittest.TestCase):
    def test_known_platforms_are_recognised(self):
        cases = {
            'https://recruiting.ultipro.com/WAD1002WADM/JobBoard/be1bb296-2bff-4732-8fa5-4c8775112887/?q=': ('ultipro', 'tenant', 'WAD1002WADM'),
            'https://boards.greenhouse.io/airbnb/jobs/82': ('greenhouse', 'slug', 'airbnb'),
            'https://jobs.lever.co/palantir/abc': ('lever', 'slug', 'palantir'),
            'https://jobs.ashbyhq.com/linear/d3bc': ('ashby', 'slug', 'linear'),
            'https://socialdriver.bamboohr.com/jobs/view.php?id=1': ('bamboohr', 'slug', 'socialdriver'),
            'https://apply.workable.com/the-ripple-way/j/ABC123/': ('workable', 'slug', 'the-ripple-way'),
            f'https://recruiting.paylocity.com/recruiting/jobs/All/{GUID}/Available-Positions': ('paylocity', 'guid', GUID),
            'https://workforcenow.adp.com/mascsr/default/mdf/recruitment/recruitment.html?cid=47ca&ccId=92_2&jobId=5': ('adp', 'cid', '47ca'),
        }
        for url, (system, field, value) in cases.items():
            config = identify(url)
            self.assertEqual((config['system'], config[field]), (system, value), url)

    def test_ordinary_pages_and_platform_marketing_pages_are_not_boards(self):
        for url in ('https://www.example.com/careers', 'https://www.bamboohr.com/careers/application',
                    'https://boards.greenhouse.io/embed/job_board', 'https://apply.workable.com/', 'not a url'):
            self.assertIsNone(identify(url), url)

    def test_unreadable_platforms_are_named(self):
        self.assertEqual(identify_unreadable('https://jobs.dayforcehcm.com/en-US/baltimoreravens/CANDIDATEPORTAL')['system'], 'dayforce')
        self.assertEqual(identify_unreadable('https://jobs.jobvite.com/acme/job/1')['system'], 'jobvite')
        self.assertIsNone(identify_unreadable('https://www.example.com/'))

    def test_names_come_from_the_address(self):
        self.assertEqual(pretty_name({'system': 'workable', 'slug': 'the-ripple-way'}), 'The Ripple Way')


class Adapters(unittest.TestCase):
    def test_paylocity_reads_the_embedded_job_list(self):
        page = '<script>window.pageData = ' + json.dumps({'Jobs': [
            {'JobId': 7, 'JobTitle': 'Graphic Designer', 'JobLocation': {'City': 'Rockville', 'State': 'MD', 'Country': 'USA'},
             'Description': '<p>Design</p>', 'PublishedDate': '2026-09-01T00:00:00'}]}) + ';</script>'
        http = SimpleNamespace(get=lambda url, params=None, accept=None: Response(status=200, text=page))
        employer = Employer({'name': 'Ripple', 'system': 'paylocity', 'guid': GUID})
        found = employer.find_openings(['Graphic Designer'], http)
        self.assertEqual((found[0]['location'], found[0]['url']), ('Rockville, MD, US', 'https://recruiting.paylocity.com/recruiting/jobs/Details/7'))
        self.assertEqual(employer.status(found[0]['url'], http), 'Open')
        self.assertEqual(employer.status('https://recruiting.paylocity.com/recruiting/jobs/Details/8', http), 'Closed')

    def test_adp_lists_and_builds_the_public_job_link(self):
        job = {'itemID': '99', 'clientRequisitionID': '5', 'requisitionTitle': 'Web Designer', 'postDate': '2026-09-02T00:00:00Z',
               'requisitionLocations': [{'address': {'cityName': 'Baltimore', 'countrySubdivisionLevel1': {'codeValue': 'MD'}}}]}

        def get(url, params=None, accept=None):
            if url.endswith('/99'):
                return Response({'requisitionDescription': '<p>Build sites</p>'})
            return Response({'jobRequisitions': [job]})
        employer = Employer({'name': 'Acme', 'system': 'adp', 'cid': 'c1', 'ccId': 'cc1'})
        found = employer.find_openings(['Web Designer'], SimpleNamespace(get=get))
        self.assertEqual(found[0]['location'], 'Baltimore, MD, US')
        self.assertIn('jobId=5', found[0]['url'])
        self.assertIn('Build sites', found[0]['html'])

    def test_workable_searches_then_reads_the_detail(self):
        place = {'city': 'Austin', 'region': 'Texas', 'countryCode': 'US'}
        http = SimpleNamespace(
            post_json=lambda url, payload: Response({'results': [{'title': 'UX Designer', 'shortcode': 'AB12', 'location': place}]}),
            get=lambda url, params=None, accept=None: Response({'title': 'UX Designer', 'description': '<p>x</p>',
                                                                'workplace': 'remote', 'location': place}))
        found = Employer({'name': 'Co', 'system': 'workable', 'slug': 'co'}).find_openings(['UX Designer'], http)
        self.assertEqual((found[0]['url'], found[0]['type']), ('https://apply.workable.com/co/j/AB12/', 'Remote'))


class Persistence(unittest.TestCase):
    def test_boards_are_deduped_and_capped(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'discovered.json'
            for slug in ('a', 'b', 'c', 'a'):
                save_discovered({'system': 'greenhouse', 'slug': slug, 'name': slug.upper()}, path=path, cap=2)
            self.assertEqual(len(json.loads(path.read_text(encoding='utf-8'))), 2)
            employers = load_employers(path=Path(folder) / 'none.json', discovered_path=path)
            self.assertTrue(all(employer.discovered for employer in employers[-2:]))
            self.assertEqual(config_key({'system': 'Greenhouse', 'slug': 'A'}), config_key({'system': 'greenhouse', 'slug': 'a'}))


class DiscoveryFlow(unittest.TestCase):
    def test_a_board_found_in_a_web_result_is_searched_and_remembered(self):
        remembered, made = [], []

        def build(config):
            made.append(config)
            return FakeEmployer([employer_opening(['Baltimore, MD, US'])])
        saved = run_search({}, 'https://boards.greenhouse.io/acme/jobs/1', 'Web Designer', max_new=3,
                           extra={'Employer': build, 'save_discovered': lambda config: remembered.append(config)})
        self.assertEqual(made[0]['slug'], 'acme')
        self.assertEqual(remembered[0]['name'], 'Acme')
        self.assertIn('https://acme.example/jobs/1', saved)


if __name__ == '__main__':
    unittest.main()
