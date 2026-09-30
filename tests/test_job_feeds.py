import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import job_feeds
from job_feeds import FEEDS, FEED_NAMES, Feed, parse_we_work_remotely

REMOTIVE_JSON = {'jobs': [
    {'id': 1, 'url': 'https://remotive.com/remote-jobs/design/web-designer-1', 'title': 'Web Designer',
     'company_name': 'Acme', 'candidate_required_location': 'USA, Canada', 'description': '<p>Design sites with Figma.</p>',
     'publication_date': '2026-09-29T10:00:00', 'salary': '$60k'},
    {'id': 2, 'url': 'https://remotive.com/remote-jobs/design/web-designer-2', 'title': 'Web Designer',
     'company_name': 'EuroCo', 'candidate_required_location': 'Europe', 'description': '', 'publication_date': ''},
    {'id': 3, 'url': 'https://remotive.com/remote-jobs/design/graphic-designer-3', 'title': 'Graphic Designer',
     'company_name': 'Globex', 'candidate_required_location': 'Worldwide', 'description': ''},
    {'id': 4, 'url': 'https://elsewhere.example.com/x', 'title': 'Web Designer', 'company_name': 'Odd',
     'candidate_required_location': 'USA', 'description': ''},
]}

RSS = '''<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:media="http://search.yahoo.com/mrss/"><channel>
<item><media:content url="https://x/logo.gif" type="image/png"/><title>Acme Robotics: Senior Web Designer</title>
<region>Anywhere in the World</region><category>Design</category><type>Full-Time</type>
<description>&lt;p&gt;Use Figma and CSS.&lt;/p&gt;</description><pubDate>Tue, 29 Sep 2026 16:16:31 +0000</pubDate>
<expires_at>Thu, 29 Oct 2099 16:16:31 +0000</expires_at><guid>https://weworkremotely.com/remote-jobs/acme-senior-web-designer</guid>
<link>https://weworkremotely.com/remote-jobs/acme-senior-web-designer</link></item>
<item><title>Old Co: Web Designer</title><region>USA Only</region><expires_at>Thu, 29 Oct 2020 16:16:31 +0000</expires_at>
<link>https://weworkremotely.com/remote-jobs/old-web-designer</link></item>
<item><title>Europa GmbH: Web Designer</title><region>Europe Only</region><pubDate>Tue, 29 Sep 2026 16:16:31 +0000</pubDate>
<link>https://weworkremotely.com/remote-jobs/europa-web-designer</link></item>
<item><title>Just A Title Without A Company</title><region>USA Only</region>
<link>https://weworkremotely.com/remote-jobs/just-a-title</link></item>
</channel></rss>'''.encode('utf-8')


class FakeResponse:
    def __init__(self, payload=None, content=b''):
        self.payload, self.content = payload, content

    def raise_for_status(self): pass

    def json(self): return self.payload


class Remotive(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        patcher = patch.object(job_feeds, 'CACHE_DIR', Path(self.directory.name))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.calls = []

    def get(self, *args, **kwargs):
        self.calls.append(kwargs['params']['category'])
        return FakeResponse(REMOTIVE_JSON)

    def test_matching_titles_open_to_us_applicants_come_back_with_the_providers_link(self):
        feed = next(item for item in FEEDS if item.name == 'Remotive')
        with patch.object(job_feeds.requests, 'get', self.get):
            jobs = job_feeds.fetch_remotive()
        found = list(feed.matching(jobs, ['Web Designer', 'Graphic Designer'], True))
        self.assertEqual([(j['name'], j['url'], j['usa_score']) for j in found],
                         [('Acme', 'https://remotive.com/remote-jobs/design/web-designer-1', 6),
                          ('Globex', 'https://remotive.com/remote-jobs/design/graphic-designer-3', 5)])
        self.assertEqual(found[0]['text'], 'Design sites with Figma.')

    def test_a_job_seen_in_two_categories_is_listed_once(self):
        with patch.object(job_feeds.requests, 'get', self.get):
            jobs = job_feeds.fetch_remotive()
        self.assertEqual(len(jobs), 4)

    def test_it_asks_remotive_at_most_once_per_category_per_day(self):
        with patch.object(job_feeds.requests, 'get', self.get):
            job_feeds.fetch_remotive()
            job_feeds.fetch_remotive()
        self.assertEqual(self.calls, list(job_feeds.REMOTIVE_CATEGORIES))

    def test_after_a_day_it_asks_again_and_old_data_covers_an_outage(self):
        with patch.object(job_feeds.requests, 'get', self.get):
            job_feeds.fetch_remotive()
        later = job_feeds.time.time() + job_feeds.REMOTIVE_CACHE_SECONDS + 60

        def down(*args, **kwargs):
            raise job_feeds.requests.ConnectionError('offline')
        with patch.object(job_feeds.time, 'time', lambda: later), patch.object(job_feeds.requests, 'get', down):
            self.assertEqual(len(job_feeds.fetch_remotive()), 4)

    def test_nothing_cached_and_no_connection_is_an_error(self):
        def down(*args, **kwargs):
            raise job_feeds.requests.ConnectionError('offline')
        with patch.object(job_feeds.requests, 'get', down):
            with self.assertRaises(ValueError):
                job_feeds.fetch_remotive()


class WeWorkRemotely(unittest.TestCase):
    def test_company_and_title_are_split_and_expired_jobs_are_dropped(self):
        jobs = parse_we_work_remotely(RSS)
        self.assertEqual([(j['company'], j['position']) for j in jobs],
                         [('Acme Robotics', 'Senior Web Designer'), ('Europa GmbH', 'Web Designer'),
                          ('', 'Just A Title Without A Company')])
        self.assertEqual(jobs[0]['posted'], '2026-09-29')
        self.assertIn('Figma', jobs[0]['description'])

    def test_only_us_eligible_matching_jobs_are_kept(self):
        feed = next(item for item in FEEDS if item.name == 'We Work Remotely')
        found = list(feed.matching(parse_we_work_remotely(RSS), ['Web Designer'], True))
        self.assertEqual([(j['name'], j['location'], j['usa_score']) for j in found],
                         [('Acme Robotics', 'Anywhere in the World', 5)])
        self.assertEqual(len(list(feed.matching(parse_we_work_remotely(RSS), ['Web Designer'], False))), 2)

    def test_a_broken_feed_is_a_clean_error(self):
        with self.assertRaises(ValueError):
            parse_we_work_remotely(b'<html><body>not a feed')


class Registry(unittest.TestCase):
    def test_three_feeds_are_registered(self):
        self.assertEqual(FEED_NAMES, {'Remote OK', 'Remotive', 'We Work Remotely'})

    def test_index_maps_listing_urls_to_listings(self):
        feed = Feed('X', 'x.com', lambda: [], {'x.com'})
        self.assertEqual(feed.index([{'url': 'https://x.com/1'}, {'apply_url': 'https://x.com/2'}]),
                         {'https://x.com/1': {'url': 'https://x.com/1'}, 'https://x.com/2': {'apply_url': 'https://x.com/2'}})


if __name__ == '__main__':
    unittest.main()
