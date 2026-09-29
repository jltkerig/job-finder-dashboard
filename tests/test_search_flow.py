import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import job_finder as finder


class SearchFlow(unittest.TestCase):
    def test_services_page_discovers_two_separate_openings(self):
        class Database:
            def close(self): pass

        root = 'https://example.com'
        pages = {
            root + '/services': '<meta property="og:site_name" content="Example"><a href="/careers">Careers</a>',
            root + '/careers': '<a href="/jobs/web-designer">Web Designer</a><a href="/jobs/frontend-developer">Frontend Developer</a>',
        }
        for path, title in (('/jobs/web-designer', 'Web Designer'), ('/jobs/frontend-developer', 'Frontend Developer')):
            pages[root + path] = '<script type="application/ld+json">' + json.dumps({
                '@type': 'JobPosting', 'title': title, 'url': root + path,
                'hiringOrganization': {'name': 'Example'}}) + '</script>'
        saved = []
        patches = {
            'connect_database': lambda: Database(), 'ensure_database_schema': lambda db: True,
            'rejected_posting_urls': lambda db: set(), 'prepare_city_targets': lambda db, state, cities: [],
            'start_docker_desktop': lambda: True, 'start_searxng': lambda: True,
            'stop_searxng': lambda: None, 'stop_docker_desktop': lambda: None,
            'check_searxng_timer': lambda: True, 'fetch_remote_ok_jobs': lambda: [],
            'search_searxng': lambda *args: [{'title': 'Website Design Company', 'url': root + '/services'}],
            'safe_request': lambda url: SimpleNamespace(url=url, text=pages[url]) if url in pages else None,
            'analyze_usa_location': lambda *args, **kw: {'country': 'United States', 'state': 'MD', 'score': 8, 'evidence': []},
            'save_company': lambda *args, **kw: saved.append((args, kw)) or True,
        }
        from contextlib import ExitStack
        with ExitStack() as stack:
            for name, function in patches.items():
                stack.enter_context(patch.object(finder, name, function))
            stack.enter_context(patch.object(finder, 'MAX_SEARCH_PAGES', 1))
            finder.main(job_title='Web Designer, Front End Developer', state='MD', cities_json='[]', max_new=2)
        self.assertEqual({args[5] for args, _ in saved},
                         {root + '/jobs/web-designer', root + '/jobs/frontend-developer'})


if __name__ == '__main__':
    unittest.main()
