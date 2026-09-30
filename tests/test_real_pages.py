"""Employer adapters run against saved copies of real career-site responses (tests/fixtures/real_pages.json).

The responses were recorded from the live Under Armour, Sinclair and CVS Health career sites, so a change that
breaks parsing of real pages fails here before release. To refresh them, re-record the file with the same titles
(see TITLES below) when a site changes its format.
"""
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import employer_jobs
from employer_jobs import Employer

FIXTURE = json.loads((Path(__file__).resolve().parent / 'fixtures' / 'real_pages.json').read_text(encoding='utf-8'))
TITLES = {'Under Armour': ['Digital Designer'], 'Sinclair': ['Graphic Designer'], 'CVS Health': ['Senior Content Designer']}


class Replay:
    """Answers from the recording; a request that was not recorded fails the test instead of reaching the network."""

    def __init__(self, tape):
        self.tape = tape

    def _answer(self, key):
        if key not in self.tape:
            raise AssertionError('Request was not in the recording: ' + key[:160])
        entry = self.tape[key]

        def as_json():
            if 'json' not in entry:
                raise ValueError('not JSON')
            return entry['json']
        return SimpleNamespace(status_code=entry['status'], url=entry.get('url', ''), text=entry.get('text', ''), json=as_json)

    def get(self, url, params=None, accept=None):
        return self._answer('GET ' + url + ' ' + json.dumps(params or {}, sort_keys=True))

    def post_json(self, url, payload):
        return self._answer('POST ' + url + ' ' + json.dumps(payload, sort_keys=True))


def openings(name):
    recorded = FIXTURE[name]
    employer = Employer(recorded['config'])
    # The recordings were made with one results page and two postings read per search.
    with patch.object(employer_jobs, 'MAX_PAGES', 1), patch.object(employer_jobs, 'MAX_DETAILS', 2):
        return employer, employer.find_openings(TITLES[name], Replay(recorded['tape']))


class RealPages(unittest.TestCase):
    def test_every_recording_still_parses_to_the_same_openings(self):
        for name, recorded in FIXTURE.items():
            _, found = openings(name)
            self.assertEqual(len(found), len(recorded['expect']), name)
            for opening, expected in zip(found, recorded['expect']):
                actual = {key: (sorted(opening[key]) if key == 'remote_states' else opening[key]) for key in expected}
                self.assertEqual(actual, expected, name)

    def test_under_armour_lead_digital_designer_is_in_baltimore(self):
        _, found = openings('Under Armour')
        self.assertEqual(found[0]['title'], 'Lead, Digital Designer (Apparel & Footwear) - Quick-to-Market')
        self.assertEqual(found[0]['location'], 'Baltimore, MD, US')
        self.assertTrue(found[0]['description'])

    def test_sinclair_oracle_posting_has_a_place_and_description(self):
        _, found = openings('Sinclair')
        self.assertEqual(found[0]['title'], 'Multimedia Graphic Designer')
        self.assertIn('CA', found[0]['location'])
        self.assertTrue(found[0]['url'].startswith('https://'))

    def test_cvs_work_at_home_locations_become_remote_state_limits(self):
        _, found = openings('CVS Health')
        self.assertEqual(found[0]['type'], 'Remote')
        self.assertEqual(found[0]['remote_states'], {'MA', 'NC', 'FL', 'RI'})


if __name__ == '__main__':
    unittest.main()
