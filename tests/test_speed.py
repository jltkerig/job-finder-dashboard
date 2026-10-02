import sys
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import no_database  # noqa: F401  (cuts tests off from the real database)
import job_finder as finder
from jobfinder.search import docker
from jobfinder.search import shared


class HostPacing(unittest.TestCase):
    def setUp(self):
        shared._host_next_request.clear()

    def test_different_sites_do_not_wait_for_each_other(self):
        with patch.object(shared, 'REQUEST_DELAY', 5), patch.object(finder.time, 'sleep') as sleep:
            finder.wait_for_host('https://a.example/1')
            finder.wait_for_host('https://b.example/1')
        sleep.assert_not_called()

    def test_the_same_site_is_spaced_by_the_delay(self):
        with patch.object(shared, 'REQUEST_DELAY', 5), patch.object(finder.time, 'sleep') as sleep:
            finder.wait_for_host('https://a.example/1')
            finder.wait_for_host('https://a.example/2')
        self.assertEqual(sleep.call_count, 1)
        self.assertGreater(sleep.call_args[0][0], 4)


class Prefetch(unittest.TestCase):
    def setUp(self):
        shared._page_cache.clear()

    def tearDown(self):
        shared._page_cache.clear()

    def test_pages_download_together_and_land_in_the_cache(self):
        active, peak, lock = [0], [0], threading.Lock()

        def slow(url):
            with lock:
                active[0] += 1
                peak[0] = max(peak[0], active[0])
            time.sleep(0.05)
            with lock:
                active[0] -= 1
            finder.remember_page(finder.canonical_url(url), SimpleNamespace(url=url, text='x'))
            return SimpleNamespace(url=url, text='x')
        urls = [f'https://site{n}.example/jobs' for n in range(6)]
        with patch.object(finder, 'safe_request', slow), patch.object(shared, 'update_existing_mode', False):
            finder.prefetch_pages(urls)
        self.assertGreater(peak[0], 1)
        self.assertTrue(all(finder.canonical_url(url) in shared._page_cache for url in urls))

    def test_a_failed_download_is_remembered_so_it_is_not_retried(self):
        urls = ['https://down.example/a', 'https://down.example/b']
        with patch.object(finder, 'safe_request', lambda url: None), patch.object(docker, 'check_searxng_timer', lambda: True), \
                patch.object(shared, 'update_existing_mode', False):
            finder.prefetch_pages(urls)
        self.assertTrue(all(shared._page_cache[finder.canonical_url(url)] is None for url in urls))

    def test_the_cache_drops_its_oldest_page_when_full(self):
        with patch.object(shared, 'update_existing_mode', False):
            for n in range(251):
                finder.remember_page(f'https://x.example/{n}', n)
        self.assertEqual(len(shared._page_cache), 250)
        self.assertNotIn('https://x.example/0', shared._page_cache)




class UpdatePrefetch(unittest.TestCase):
    def setUp(self):
        shared._prefetched.clear()

    def tearDown(self):
        shared._prefetched.clear()
        shared._last_failure_status = None

    def test_rows_read_pages_that_were_downloaded_ahead_and_only_once(self):
        calls = []

        def download(url, key, failure=None):
            calls.append(url)
            if 'gone' in url:
                if failure is not None:
                    failure.append(404)
                return None
            return SimpleNamespace(url=url, text='page ' + url)
        urls = ['https://a.example/job', 'https://b.example/job', 'https://gone.example/job']
        with patch.object(finder, '_fetch_page', download), patch.object(shared, 'update_existing_mode', True):
            finder.prefetch_for_update(urls)
            self.assertEqual(sorted(calls), sorted(urls))
            self.assertEqual(finder.safe_request('https://a.example/job').text, 'page https://a.example/job')
            self.assertIsNone(finder.safe_request('https://gone.example/job'))
            self.assertEqual(shared._last_failure_status, 404)  # the caller can still tell a removed page apart
            finder.safe_request('https://a.example/job')  # already handed out: downloaded fresh, not reused
        self.assertEqual(calls.count('https://a.example/job'), 2)

    def test_a_single_page_is_not_worth_a_pool(self):
        with patch.object(finder, '_fetch_page', lambda *a, **k: self.fail('should not download')):
            finder.prefetch_for_update(['https://a.example/job'])


class DirectPostings(unittest.TestCase):
    PAGE = SimpleNamespace(url='https://jobs.example/opening/1', text='<html><title>Web Designer | Acme</title><a href="/about">About</a>'
                                                                       '<p>Baltimore, MD</p></html>')

    def inspect(self, deep):
        calls = []
        with patch.object(finder, 'safe_request', lambda url: calls.append(url) or self.PAGE), \
                patch.object(finder, 'discover_support_links', lambda soup, url: ['https://jobs.example/about']), \
                patch.object(finder, 'discover_sitemap_links', lambda url: ['https://jobs.example/sitemap.xml']), \
                patch.object(finder, 'discover_robots_links', lambda url: []), \
                patch.object(finder, 'find_external_company_site', lambda soup, url: None):
            finder.inspect_company_site('https://jobs.example/opening/1', 'Web Designer', 'jobs.example', deep=deep)
        return calls

    def test_a_direct_posting_is_read_without_the_sites_other_pages(self):
        self.assertEqual(self.inspect(deep=False), ['https://jobs.example/opening/1'])

    def test_a_possible_lead_still_gets_the_deep_read(self):
        self.assertEqual(len(self.inspect(deep=True)), 3)


class NamesAndBoards(unittest.TestCase):
    def test_a_leading_entity_code_is_dropped_from_the_company_name(self):
        self.assertEqual(finder.tidy_company_name('003 Humana Inc.'), 'Humana Inc.')
        self.assertEqual(finder.tidy_company_name('3M Company'), '3M Company')
        self.assertEqual(finder.tidy_company_name('84 Lumber'), '84 Lumber')  # only zero-padded codes are dropped

    def test_recognised_applicant_boards_count_as_official(self):
        self.assertTrue(finder.on_official_board('https://humana.wd5.myworkdayjobs.com/en-US/Humana_External_Career_Site/job/Remote/x_R-1'))
        self.assertTrue(finder.on_official_board('https://boards.greenhouse.io/acme/jobs/1'))
        self.assertFalse(finder.on_official_board('https://example.com/careers/designer'))


if __name__ == '__main__':
    unittest.main()
