import json
import sys
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import no_database  # noqa: F401  (cuts tests off from the real database)
import job_finder as finder
from owners import holders
from jobfinder.search import docker
from jobfinder.search import shared


class Database:
    def close(self): pass


def run_search(pages, search_url, job_title, *, cities_json='[]', max_new=2, location=None,
               city_targets=(), distance=None, extra=None, records=None, debug_out=None):
    """Run main() against in-memory pages and return the saved source URLs.

    records collects each save_company call's positional arguments; debug_out collects the debug file.
    """
    location = location or (lambda *args, **kw: {'country': 'United States', 'state': 'MD', 'score': 8, 'evidence': []})
    saved = []

    def save(*args, **kw):
        saved.append(args[6])  # source_url, the unique key
        if records is not None:
            records.append(args)
        return True

    patches = {
        'connect_database': lambda: Database(), 'ensure_database_schema': lambda db: True,
        'rejected_posting_urls': lambda db: set(), 'prepare_city_targets': lambda db, state, cities: list(city_targets),
        'start_docker_desktop': lambda: True, 'start_searxng': lambda: True,
        'stop_searxng': lambda: None, 'stop_docker_desktop': lambda: None,
        'check_searxng_timer': lambda: True, 'FEEDS': (), 'JOB_SITES': (), 'load_employers': lambda: [], 'company_board_posting': lambda name, title: None, 'fetch_text': lambda *a, **k: None,
        'search_searxng': lambda *args: [{'title': 'Result', 'url': search_url}],
        'safe_request': lambda url: SimpleNamespace(url=url, text=pages[url]) if url in pages else None,
        'analyze_usa_location': location,
        'save_company': save,
        'record_decision': lambda *args, **kw: None,
        'apply_link_closed': lambda *args: None,
    }
    if distance is not None:
        patches['distance_to_city_targets'] = distance
    patches.update(extra or {})
    with ExitStack() as stack:
        debug_file = Path(stack.enter_context(tempfile.TemporaryDirectory())) / 'search_debug.json'
        stack.enter_context(patch.object(shared, 'SEARCH_DEBUG_FILE', debug_file))
        stack.enter_context(patch.object(finder, 'BOARD_HEALTH_FILE', debug_file.with_name('board_health.json')))
        for name, function in patches.items():
            for module in holders(name):
                stack.enter_context(patch.object(module, name, function))
        stack.enter_context(patch.object(shared, 'MAX_SEARCH_PAGES', 1))
        try:
            finder.main(job_title=job_title, state='MD', cities_json=cities_json, max_new=max_new)
        finally:
            if debug_out is not None and debug_file.exists():
                debug_out.append(json.loads(debug_file.read_text(encoding='utf-8')))
    return set(saved)


def posting(url, title, remote=False, company='Example'):
    data = {'@type': 'JobPosting', 'title': title, 'url': url, 'hiringOrganization': {'name': company}}
    if remote:
        data['jobLocationType'] = 'TELECOMMUTE'
    return '<script type="application/ld+json">' + json.dumps(data) + '</script>'


class SearchFlow(unittest.TestCase):
    def test_services_page_discovers_two_separate_openings(self):
        root = 'https://example.com'
        pages = {
            root + '/services': '<meta property="og:site_name" content="Example"><a href="/careers">Careers</a>',
            root + '/careers': '<a href="/jobs/web-designer">Web Designer</a><a href="/jobs/frontend-developer">Frontend Developer</a>',
        }
        for path, title in (('/jobs/web-designer', 'Web Designer'), ('/jobs/frontend-developer', 'Frontend Developer')):
            pages[root + path] = posting(root + path, title)
        saved = run_search(pages, root + '/services', 'Web Designer, Front End Developer')
        self.assertEqual(saved, {root + '/jobs/web-designer', root + '/jobs/frontend-developer'})

    def test_pdf_results_are_never_fetched_or_recorded(self):
        fetched, skips = [], []
        pdf = 'https://example.com/files/web-designer-job.pdf'
        run_search({}, pdf, 'Web Designer', extra={
            'safe_request': lambda url: fetched.append(url),
            'record_skip': lambda *args: skips.append(args),
        })
        self.assertEqual((fetched, skips), ([], []))


class FeedSearch(unittest.TestCase):
    """Remote-job feeds (Remote OK, Remotive, We Work Remotely) are searched before the web."""

    def job(self, number=1, description='<p>Work from anywhere.</p>'):
        return {'id': str(number), 'position': 'Web Designer', 'company': f'Acme {number}', 'location': 'USA',
                'url': f'https://remotive.com/remote-jobs/design/web-designer-{number}', 'description': description}

    def run_feed(self, jobs=None, fetch=None, max_new=2):
        from jobfinder.sources.job_feeds import Feed
        feed = Feed('Remotive', 'remotive.com', fetch or (lambda: jobs), {'remotive.com'})
        captured, skips = [], []
        run_search({}, 'https://example.com/none', 'Web Designer', max_new=max_new,
                   cities_json=json.dumps([{'city': 'Maryland', 'radius': 50}]),
                   extra={'FEEDS': (feed,), 'save_company': lambda *a, **kw: captured.append((a, kw)) or True,
                          'record_skip': lambda *a: skips.append(a[0])})
        return captured, skips

    def test_a_feed_job_is_saved_under_its_provider_with_the_providers_link(self):
        captured, _ = self.run_feed([self.job()])
        args, kwargs = captured[0]
        self.assertEqual((args[4], args[5], args[6]), ('remotive.com',) + (self.job()['url'],) * 2)
        self.assertEqual((kwargs['source_type'], kwargs['work_arrangement']), ('Remotive', 'Remote'))
        self.assertEqual(kwargs['listing_details']['evidence'], ['Remotive feed', 'matching title'])

    def test_a_remote_job_limited_to_another_state_is_skipped(self):
        captured, skips = self.run_feed([self.job(description='<p>You must reside in Texas.</p>')])
        self.assertEqual(captured, [])
        self.assertIn('Remote job limited to residents of TX', skips)

    def test_a_feed_can_be_switched_off_in_settings(self):
        with patch.object(shared, 'settings', dict(shared.settings, remote_feeds={'Remotive': False})):
            captured, _ = self.run_feed([self.job()])
        self.assertEqual(captured, [])

    def test_a_remote_job_limited_to_a_selected_state_is_kept(self):
        captured, _ = self.run_feed([self.job(description='<p>You must reside in Maryland.</p>')])
        self.assertEqual(len(captured), 1)

    def test_a_feed_that_is_down_does_not_stop_the_search(self):
        def down():
            raise ValueError('Remotive is unavailable')
        captured, _ = self.run_feed(fetch=down)
        self.assertEqual(captured, [])

    def test_feeds_cannot_fill_every_slot(self):
        captured, _ = self.run_feed([self.job(n) for n in (1, 2, 3)], max_new=2)
        self.assertEqual(len(captured), 1)


class FakeEmployer:
    name, domain, extra_titles = 'Acme Bank', 'acmebank.com', []
    config = {'system': 'workday', 'host': 'acme.example', 'tenant': 'acme', 'site': 'careers'}
    discovered = False
    adapter = SimpleNamespace(base='https://acme.example/api')

    def __init__(self, openings=None, error=None):
        self._openings, self._error = openings or [], error

    def find_openings(self, titles, http):
        if self._error:
            raise self._error
        return self._openings


def employer_opening(locations, arrangement=None, country='United States of America', remote_states=(), description='<p>Design.</p>'):
    return {'title': 'Web Designer', 'url': 'https://acme.example/jobs/1', 'company': 'Acme Bank', 'location': locations[0],
            'locations': locations, 'type': arrangement, 'schedule': 'FULL_TIME', 'salary': '', 'posted': '2026-09-20',
            'evidence': ['Acme Bank careers site (Workday)', 'title matches search'], 'description': 'Design.', 'html': description,
            'country': country, 'remote_states': set(remote_states), 'category': ''}


class EmployerSearch(unittest.TestCase):
    """Big employers' own career sites go through the same location, remote and U.S. checks as web results."""
    CITIES = json.dumps([{'city': 'Baltimore, MD', 'radius': 20}])

    def search(self, employer, distance=None):
        captured, skips = [], []
        near_baltimore = distance or (lambda db, html, place, state, targets, **kw: (
            ('Baltimore' in place), 'Baltimore', 39.29, -76.61, 0.0) if 'Baltimore' in place else (False, 'Atlanta', 33.7, -84.4, 600.0))
        run_search({}, 'https://example.com/none', 'Web Designer', max_new=3, cities_json=self.CITIES,
                   city_targets=[{'city': 'Baltimore, MD', 'radius': 20}], distance=near_baltimore,
                   extra={'load_employers': lambda: [employer],
                          'save_company': lambda *a, **kw: captured.append((a, kw)) or True,
                          'record_skip': lambda *a: skips.append(a[0])})
        return captured, skips

    def test_a_job_near_a_selected_city_is_saved_under_the_employer(self):
        captured, _ = self.search(FakeEmployer([employer_opening(['Baltimore, MD, US'])]))
        args, kwargs = captured[0]
        self.assertEqual((args[1], args[4], args[5], args[6]), ('Acme Bank', 'acmebank.com') + ('https://acme.example/jobs/1',) * 2)
        self.assertEqual(kwargs['source_type'], 'Employer careers')
        self.assertEqual((kwargs['city'], kwargs['distance_miles']), ('Baltimore', 0.0))
        self.assertEqual(kwargs['listing_details']['matched_title'], 'Web Designer')

    def test_a_job_far_from_every_selected_city_is_skipped(self):
        captured, skips = self.search(FakeEmployer([employer_opening(['Atlanta, GA, US'])]))
        self.assertEqual(captured, [])
        self.assertIn('Outside selected location', skips)

    def test_a_posting_with_several_places_passes_when_any_one_fits(self):
        captured, _ = self.search(FakeEmployer([employer_opening(['Atlanta, GA, US', 'Baltimore, MD, US'])]))
        self.assertEqual(len(captured), 1)
        self.assertEqual(captured[0][1]['listing_details']['location'], 'Baltimore, MD, US')

    def test_a_remote_job_for_residents_of_another_state_is_skipped_but_a_selected_state_is_kept(self):
        _, skips = self.search(FakeEmployer([employer_opening(['TEXAS - VIRTUAL - TX01, US'], 'Remote', remote_states={'TX'})]))
        self.assertIn('Remote job limited to residents of TX', skips)
        captured, _ = self.search(FakeEmployer([employer_opening(['MARYLAND - VIRTUAL - MD01, US'], 'Remote', remote_states={'MD'})]))
        self.assertEqual(len(captured), 1)

    def test_jobs_outside_the_us_are_skipped(self):
        captured, skips = self.search(FakeEmployer([employer_opening(['Paris, France'], country='France')]))
        self.assertEqual(captured, [])
        self.assertIn('Posting restricts applicants outside the US', skips)

    def test_an_employer_site_that_is_down_does_not_stop_the_search(self):
        captured, _ = self.search(FakeEmployer(error=ValueError('Workday answered HTTP 500')))
        self.assertEqual(captured, [])


class EmployerSites(unittest.TestCase):
    JOB = 'https://www.jobleads.com/us/job/ux-driven-web-designer-front-end-specialist--towson--e9c63'
    TITLE = 'UX-Driven Web Designer & Front-End Specialist'
    HOME = '<meta property="og:site_name" content="YOUCANIC"><a href="/careers/">Careers</a>'
    CAREERS = '<title>Careers | YOUCANIC</title><h1>Careers</h1>'

    def search(self, extra_pages=None, company='YOUCANIC'):
        pages = {self.JOB: posting(self.JOB, self.TITLE, company=company)}
        pages.update(extra_pages or {})
        records, debug = [], []
        saved = run_search(pages, self.JOB, 'Web Designer', max_new=1, records=records, debug_out=debug)
        return saved, records, debug[0]['runs'][-1]

    def test_job_board_listing_keeps_its_own_link_and_names_the_employers_site(self):
        saved, records, run = self.search({'https://www.youcanic.com/': self.HOME,
                                           'https://www.youcanic.com/careers/': self.CAREERS})
        args = records[0]
        # View goes to the job itself; a careers page that does not list it is shown as the employer's site only.
        self.assertEqual((args[4], args[5], args[6]), ('youcanic.com', self.JOB, self.JOB))
        self.assertEqual(saved, {self.JOB})
        lead = run['leads'][0]
        self.assertEqual(lead['view_link'], self.JOB)
        self.assertEqual(lead['employer_site']['careers_url'], 'https://www.youcanic.com/careers/')
        self.assertEqual(lead['employer_site']['method'], 'domain guess')
        self.assertEqual(lead['found_by_query'], run['queries'][0]['query'])

    def test_a_job_board_repost_whose_apply_link_is_expired_is_not_saved(self):
        skips = []
        closed = {'reason': 'its Apply link redirects to an expired-job page', 'url': 'https://x.example/expired',
                  'apply_link': 'https://x.example/redirect?jobid=1'}
        pages = {self.JOB: posting(self.JOB, self.TITLE, company='YOUCANIC')}
        saved = run_search(pages, self.JOB, 'Web Designer', max_new=1,
                           extra={'apply_link_closed': lambda *args: closed,
                                  'record_skip': lambda *args: skips.append(args[0])})
        self.assertEqual(saved, set())
        self.assertEqual(skips, ['Job is closed (expired)'])

    def test_listing_without_a_findable_employer_is_saved_as_before(self):
        saved, records, run = self.search(company='Zzyzx Labs')
        args = records[0]
        self.assertEqual((args[4], args[5], args[6]), ('jobleads.com', self.JOB, self.JOB))
        self.assertIsNone(run['leads'][0]['employer_site'])
        self.assertTrue(run['leads'][0]['employer_notes'])


class DebugFile(unittest.TestCase):
    def test_inputs_queries_and_stop_reason_are_recorded(self):
        root = 'https://example.com'
        pages = {root + '/jobs/web-designer': posting(root + '/jobs/web-designer', 'Web Designer')}
        debug = []
        run_search(pages, root + '/jobs/web-designer', 'Web Designer', max_new=1, debug_out=debug,
                   cities_json=json.dumps([{'city': 'Maryland', 'radius': 50}]))
        run = debug[0]['runs'][-1]
        self.assertEqual(run['inputs']['typed_titles'], ['Web Designer'])
        self.assertEqual(run['inputs']['statewide_states'], ['MD'])
        self.assertIn('1 of 1 distinct jobs found', run['stop_reason'])
        self.assertEqual(run['leads'][0]['matched_title'], 'Web Designer')
        self.assertGreaterEqual(run['queries'][0]['results'], 1)

    def test_crash_still_leaves_a_debug_record(self):
        debug = []
        def crash(*args):
            raise RuntimeError('boom')
        with self.assertRaises(RuntimeError):
            run_search({}, 'https://example.com', 'Web Designer', debug_out=debug, extra={'search_searxng': crash})
        self.assertIn('RuntimeError: boom', debug[0]['runs'][-1]['stop_reason'])


class LocationFilter(unittest.TestCase):
    URL = 'https://example.com/jobs/web-designer'
    STATEWIDE_MD = json.dumps([{'city': 'Maryland', 'radius': 50}])
    MD_AND_PHILLY = json.dumps([{'city': 'Maryland', 'radius': 50}, {'city': 'Philadelphia, PA', 'radius': 20}])

    def search(self, state, cities_json, remote=False, **kw):
        location = lambda *args, **k: {'country': 'United States', 'state': state, 'score': 8, 'evidence': []}
        pages = {self.URL: posting(self.URL, 'Web Designer', remote=remote)}
        return run_search(pages, self.URL, 'Web Designer', cities_json=cities_json, max_new=1,
                          location=location, **kw)

    def test_statewide_match_is_kept_when_a_city_radius_is_also_selected(self):
        far_from_philly = lambda *args: (False, 'Cumberland', 39.6, -78.7, 180.0)
        saved = self.search('MD', self.MD_AND_PHILLY, city_targets=[{'city': 'Philadelphia, PA'}],
                            distance=far_from_philly)
        self.assertEqual(saved, {self.URL})

    def test_statewide_only_keeps_remote_jobs(self):
        self.assertEqual(self.search('US Remote', self.STATEWIDE_MD, remote=True), {self.URL})

    def test_statewide_only_rejects_other_states(self):
        self.assertEqual(self.search('VA', self.STATEWIDE_MD), set())


class StateDetection(unittest.TestCase):
    def test_west_virginia_is_not_virginia(self):
        self.assertEqual(finder.find_state_from_text('Charleston, West Virginia'), 'WV')
        self.assertEqual(finder.find_state_from_text('Richmond, Virginia'), 'VA')

    def test_first_mentioned_state_wins(self):
        self.assertEqual(finder.find_state_from_text('Offices in Maryland and Massachusetts'), 'MD')


class StopRequest(unittest.TestCase):
    def test_stop_file_ends_the_search_and_cleans_up(self):
        with tempfile.TemporaryDirectory() as directory:
            stop_file = Path(directory) / '.stop-requested'
            stop_file.touch()
            with patch.object(shared, 'STOP_REQUEST_FILE', stop_file):
                self.assertFalse(docker.check_searxng_timer())

    def test_crash_mid_search_still_stops_searxng(self):
        stopped = []
        def crash(*args):
            raise RuntimeError('boom')
        with self.assertRaises(RuntimeError):
            run_search({}, 'https://example.com', 'Web Designer', extra={
                'search_searxng': crash,
                'stop_searxng': lambda: stopped.append('searxng'),
                'stop_docker_desktop': lambda: stopped.append('docker'),
            })
        self.assertEqual(stopped, ['searxng', 'docker'])


if __name__ == '__main__':
    unittest.main()
