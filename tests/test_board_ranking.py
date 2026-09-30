import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import job_finder as finder
from employer_jobs import load_employers, record_board_result, save_discovered


def board(slug):
    return {'system': 'greenhouse', 'slug': slug, 'name': slug.title()}


class BoardRanking(unittest.TestCase):
    def test_boards_that_matched_come_first_and_barren_ones_are_dropped(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'found.json'
            for slug in ('quiet', 'busy', 'barren'):
                save_discovered(board(slug), path=path)
            for _ in range(5):
                record_board_result(board('barren'), 0, path=path)
            record_board_result(board('busy'), 3, path=path)
            record_board_result(board('busy'), 2, path=path)
            record_board_result(board('quiet'), 0, path=path)
            names = [employer.name for employer in load_employers(path=Path(folder) / 'none.json', discovered_path=path)
                     if employer.discovered]
            self.assertEqual(names, ['Busy', 'Quiet'])

    def test_recording_an_unknown_board_changes_nothing(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'found.json'
            record_board_result(board('ghost'), 1, path=path)
            self.assertFalse(path.exists())


class Timing(unittest.TestCase):
    def test_time_is_added_up_by_name(self):
        finder._timings.clear()
        with finder.timed('work'):
            pass
        with finder.timed('work'):
            pass
        self.assertIn('work', finder.timing_summary())
        finder._timings.clear()


if __name__ == '__main__':
    unittest.main()
