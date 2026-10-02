import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import no_database  # noqa: F401  (cuts tests off from the real database)
import search_debug
from search_debug import DebugRun


class DebugFile(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'search_debug.json'

    def runs(self):
        return json.loads(self.path.read_text(encoding='utf-8'))['runs']

    def test_only_the_last_ten_runs_are_kept(self):
        for number in range(12):
            DebugRun(self.path, 'search', '1.0').finish(f'run {number}')
        runs = self.runs()
        self.assertEqual(len(runs), 10)
        self.assertEqual([run['stop_reason'] for run in runs], [f'run {number}' for number in range(2, 12)])

    def test_long_text_and_long_lists_are_clipped(self):
        run = DebugRun(self.path, 'search', '1.0')
        run.set_inputs(text='x' * 5000, titles=[f'title {n}' for n in range(500)])
        run.finish('done')
        inputs = self.runs()[-1]['inputs']
        self.assertLessEqual(len(inputs['text']), search_debug.MAX_TEXT + 1)
        self.assertEqual(len(inputs['titles']), search_debug.MAX_ITEMS + 1)
        self.assertTrue(inputs['titles'][-1].startswith('…'))

    def test_secrets_are_never_written(self):
        run = DebugRun(self.path, 'search', '1.0')
        run.set_inputs(settings={'DB_PASSWORD': 'hunter2', 'flask_secret_key': 's', 'api_key': 'k',
                                 'access_token': 't', 'usa_only': True})
        run.finish('done')
        text = self.path.read_text(encoding='utf-8')
        for secret in ('hunter2', '"s"', '"k"', '"t"'):
            self.assertNotIn(secret, text)
        self.assertTrue(self.runs()[-1]['inputs']['settings']['usa_only'])

    def test_skips_are_counted_but_the_list_is_capped(self):
        run = DebugRun(self.path, 'search', '1.0')
        for number in range(search_debug.MAX_SKIPS + 50):
            run.skip('Outside selected location', f'https://example.com/{number}', 'Web Designer')
        run.finish('done')
        saved = self.runs()[-1]
        self.assertEqual(saved['skip_counts']['Outside selected location'], search_debug.MAX_SKIPS + 50)
        self.assertEqual(len(saved['skips']), search_debug.MAX_SKIPS)

    def test_finish_writes_once_and_a_damaged_file_is_replaced(self):
        self.path.write_text('{not json', encoding='utf-8')
        run = DebugRun(self.path, 'update', '1.0')
        run.finish('first')
        run.finish('second')
        runs = self.runs()
        self.assertEqual([item['stop_reason'] for item in runs], ['first'])
        self.assertEqual(runs[0]['mode'], 'update')

    def test_queries_track_pages_and_results(self):
        run = DebugRun(self.path, 'search', '1.0')
        run.query('"web designer" "MD" jobs')
        run.query_page(10)
        run.query_page(7)
        run.finish('done')
        self.assertEqual(self.runs()[-1]['queries'], [{'query': '"web designer" "MD" jobs', 'pages': 2, 'results': 17}])


if __name__ == '__main__':
    unittest.main()
