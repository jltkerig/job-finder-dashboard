import json
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import no_database  # noqa: F401  (cuts tests off from the real database)
from jobfinder.records import board_health
import dashboard
import job_finder as finder
from jobfinder.search import docker
from jobfinder.search import shared
from jobfinder.records.board_health import BoardHealth, classify_error, read_health

GOOD_FORM = {'searxng_timeout_minutes': '45', 'max_search_results': '10', 'max_search_pages': '20', 'request_delay_seconds': '1',
             'search_query_delay_seconds': '2.5', 'website_timeout_seconds': '15', 'parallel_page_fetches': '6',
             'stop_after_empty_queries': '8', 'usa_only': 'on'}


class Settings(unittest.TestCase):
    def test_valid_form_changes_only_known_options(self):
        current = {'remote_feeds': {'Remotive': False}, 'usa_only': True, 'start_mysql_automatically': True}
        updated, error = dashboard.apply_tuning_form(GOOD_FORM, current)
        self.assertIsNone(error)
        self.assertEqual((updated['searxng_timeout_minutes'], updated['search_query_delay_seconds']), (45, 2.5))
        self.assertEqual(updated['remote_feeds'], {'Remotive': False})
        self.assertTrue(updated['start_mysql_automatically'])
        self.assertFalse(updated['stop_docker_when_finished'])  # an unchecked box turns the option off

    def test_out_of_range_and_non_numeric_values_are_refused(self):
        for key, value in (('searxng_timeout_minutes', '0'), ('parallel_page_fetches', '99'), ('max_search_results', 'lots')):
            updated, error = dashboard.apply_tuning_form(dict(GOOD_FORM, **{key: value}), {})
            self.assertIsNone(updated)
            self.assertTrue(error)

    def test_page_shows_the_current_values_and_saving_writes_the_file(self):
        with tempfile.TemporaryDirectory() as folder:
            settings_file = Path(folder) / 'settings.json'
            settings_file.write_text(json.dumps({'searxng_timeout_minutes': 33, 'max_search_results': 10}), encoding='utf-8')
            with patch.object(dashboard, 'SETTINGS_FILE', settings_file), \
                    patch.object(dashboard, 'read_health', lambda: None):
                client = dashboard.app.test_client()
                page = client.get('/tuning')
                self.assertEqual(page.status_code, 200)
                text = page.get_data(as_text=True)
                self.assertIn('value="33"', text)
                self.assertIn('No search has recorded board results yet', text)
                token = re.search(r'name="csrf_token" value="([^"]+)"', text).group(1)
                response = client.post('/tuning/settings', data=dict(GOOD_FORM, csrf_token=token))
                self.assertEqual(response.status_code, 302)
                self.assertEqual(json.loads(settings_file.read_text(encoding='utf-8'))['searxng_timeout_minutes'], 45)
                refused = client.post('/tuning/settings', data=dict(GOOD_FORM, csrf_token=token, max_search_pages='0'))
                self.assertIn('error=', refused.headers['Location'])
                self.assertEqual(json.loads(settings_file.read_text(encoding='utf-8'))['max_search_pages'], 20)


class Health(unittest.TestCase):
    def test_errors_are_sorted_into_blocked_and_down(self):
        self.assertEqual(classify_error(ValueError('Workday answered HTTP 403')), 'blocked')
        self.assertEqual(classify_error(ValueError('Eightfold: Not authorized')), 'blocked')
        self.assertEqual(classify_error(ValueError('Workday answered HTTP 500')), 'down')

    def test_report_is_written_and_read_back(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'health.json'
            health = BoardHealth()
            health.write(path)
            self.assertIsNone(read_health(path))  # nothing noted, nothing written
            health.note('Capital One', 'Employer board', 'ok', 4, 1)
            health.failed('Ciena', 'Employer board', ValueError('HTTP 403'))
            health.note('Remotive', 'Remote feed', 'off')
            health.write(path, 'search')
            report = read_health(path)
            self.assertEqual([board['status'] for board in report['boards']], ['ok', 'blocked', 'off'])
            self.assertEqual(report['boards'][0]['saved'], 1)

    def test_the_page_lists_board_results(self):
        report = {'updated': '2026-09-30T12:00:00+00:00', 'run': 'search',
                  'boards': [{'name': 'Ciena', 'kind': 'Employer board', 'status': 'blocked', 'matches': 0, 'saved': 0, 'detail': 'HTTP 403'}]}
        with patch.object(dashboard, 'read_health', lambda: dict(report)):
            text = dashboard.app.test_client().get('/tuning').get_data(as_text=True)
        self.assertIn('Ciena', text)
        self.assertIn('<strong>Blocked</strong>', text)


class StopReasons(unittest.TestCase):
    def test_reasons_say_what_to_change(self):
        with patch.object(shared, 'stop_requested', lambda: False), patch.object(shared, 'MAX_SEARCH_RESULTS', 10), \
                patch.object(docker, 'check_searxng_timer', lambda: True):
            self.assertIn('More job titles'.lower(), docker.search_stop_reason(3, None).lower())
            self.assertEqual(docker.search_stop_reason(10, None), '10 of 10 distinct jobs found')
            self.assertEqual(docker.search_stop_reason(3, 'rate-limited'), 'rate-limited')
            with patch.object(docker, 'searxng_start_time', 0), patch.object(finder.time, 'time', lambda: 10 ** 9):
                self.assertIn('Tuning page', docker.search_stop_reason(3, None))
        with patch.object(shared, 'stop_requested', lambda: False), patch.object(shared, 'MAX_SEARCH_RESULTS', 10), \
                patch.object(docker, 'check_searxng_timer', lambda: False), patch.object(docker, 'searxng_start_time', None):
            self.assertIn('Docker', docker.search_stop_reason(3, None))
        with patch.object(shared, 'stop_requested', lambda: True):
            self.assertEqual(docker.search_stop_reason(3, None), 'Stopped by user')


if __name__ == '__main__':
    unittest.main()
