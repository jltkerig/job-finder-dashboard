import sys
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import job_finder as finder


class HostPacing(unittest.TestCase):
    def setUp(self):
        finder._host_next_request.clear()

    def test_different_sites_do_not_wait_for_each_other(self):
        with patch.object(finder, 'REQUEST_DELAY', 5), patch.object(finder.time, 'sleep') as sleep:
            finder.wait_for_host('https://a.example/1')
            finder.wait_for_host('https://b.example/1')
        sleep.assert_not_called()

    def test_the_same_site_is_spaced_by_the_delay(self):
        with patch.object(finder, 'REQUEST_DELAY', 5), patch.object(finder.time, 'sleep') as sleep:
            finder.wait_for_host('https://a.example/1')
            finder.wait_for_host('https://a.example/2')
        self.assertEqual(sleep.call_count, 1)
        self.assertGreater(sleep.call_args[0][0], 4)


class Prefetch(unittest.TestCase):
    def setUp(self):
        finder._page_cache.clear()

    def tearDown(self):
        finder._page_cache.clear()

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
        with patch.object(finder, 'safe_request', slow), patch.object(finder, 'update_existing_mode', False):
            finder.prefetch_pages(urls)
        self.assertGreater(peak[0], 1)
        self.assertTrue(all(finder.canonical_url(url) in finder._page_cache for url in urls))

    def test_a_failed_download_is_remembered_so_it_is_not_retried(self):
        urls = ['https://down.example/a', 'https://down.example/b']
        with patch.object(finder, 'safe_request', lambda url: None), patch.object(finder, 'check_searxng_timer', lambda: True), \
                patch.object(finder, 'update_existing_mode', False):
            finder.prefetch_pages(urls)
        self.assertTrue(all(finder._page_cache[finder.canonical_url(url)] is None for url in urls))

    def test_the_cache_drops_its_oldest_page_when_full(self):
        with patch.object(finder, 'update_existing_mode', False):
            for n in range(251):
                finder.remember_page(f'https://x.example/{n}', n)
        self.assertEqual(len(finder._page_cache), 250)
        self.assertNotIn('https://x.example/0', finder._page_cache)


if __name__ == '__main__':
    unittest.main()
