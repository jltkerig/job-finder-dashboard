import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import no_database  # noqa: F401  (cuts tests off from the real database)
sys.path.insert(0, str(Path(__file__).resolve().parent))
import time
from jobfinder.search import searching
from jobfinder.search import session
from jobfinder.search import shared
from test_search_flow import run_search


class BlockedEngines(unittest.TestCase):
    def search(self, fake_search, limit):
        debug = []
        saved = run_search({}, 'https://example.com', 'Web Designer', debug_out=debug,
                           extra={'search_web': fake_search, 'EMPTY_QUERY_LIMIT': limit})
        return saved, debug[0]['runs'][-1]

    def test_search_stops_early_and_says_why_when_every_query_is_empty(self):
        saved, run = self.search(lambda *args: [], 3)
        self.assertEqual(saved, set())
        self.assertEqual(len(run['queries']), 3)
        self.assertIn('returned nothing for 3 queries in a row', run['stop_reason'])
        self.assertTrue(run['summary']['search_engines_blocking'])

    def test_a_query_with_results_resets_the_count(self):
        seen = []
        def fake(query, page=1):
            if query not in seen:
                seen.append(query)
            index = seen.index(query)
            return [{'title': 'x', 'url': f'https://example.com/files/{index}.pdf'}] if page == 1 and index % 4 == 0 else []
        _, run = self.search(fake, 5)
        self.assertTrue(run['stop_reason'].startswith('Search sources exhausted'))
        self.assertGreater(len(run['queries']), 5)
        self.assertFalse(run['summary']['search_engines_blocking'])


class Pacing(unittest.TestCase):
    def call(self, clock, sleeps):
        response = SimpleNamespace(status_code=200, raise_for_status=lambda: None, json=lambda: {'web': {'results': []}})
        with patch.object(session, 'web_search_ok', lambda: True), patch.object(session, 'engine_refused', False), \
                patch.object(searching, '_brave_get', lambda params: response), \
                patch.object(time, 'monotonic', lambda: clock[0]), \
                patch.object(time, 'sleep', lambda seconds: sleeps.append(round(seconds, 2))), \
                patch.object(shared, 'QUERY_DELAY', 3):
            return searching.search_web('web designer jobs')

    def test_requests_are_spaced_out(self):
        sleeps, clock = [], [100.0]
        with patch.object(searching, '_last_search_time', 0.0):
            self.call(clock, sleeps)      # nothing to wait for
            clock[0] = 101.0
            self.call(clock, sleeps)      # only 1 second has passed, so wait 2 more
        self.assertEqual(sleeps, [2.0])


if __name__ == '__main__':
    unittest.main()
