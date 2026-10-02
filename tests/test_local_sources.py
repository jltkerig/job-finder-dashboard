import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import no_database  # noqa: F401  (cuts tests off from the real database)
sys.path.insert(0, str(Path(__file__).resolve().parent))
import job_finder as finder
from jobfinder.search import relevance
from jobfinder.search import company_names
from jobfinder.search import shared
from jobfinder.sources.ats_discovery import identify
from jobfinder.sources.employer_jobs import DEFAULT_EMPLOYERS, Employer, config_key
from test_employer_jobs import Response
from test_search_flow import FakeEmployer, employer_opening, run_search

LIST_PAGE = '''<ul class="search-results-listing-container">
<li class="list-item" data-job-id="501"><h3><a class="item-details-link" href="/careers/baltimorecounty/jobs/501/graphic-designer">Graphic Designer</a></h3>
<ul class="list-meta"><li>Towson, MD</li><li>MERIT - $60,000 Annually</li></ul></li>
<li class="list-item" data-job-id="502"><h3><a class="item-details-link" href="/careers/baltimorecounty/jobs/502/engineer-sewer-design">Engineer - Sewer Design</a></h3>
<ul class="list-meta"><li>Towson, MD</li></ul></li></ul>'''
POSTING = {'@context': 'https://schema.org/', '@type': 'JobPosting', 'title': 'Graphic Designer', 'datePosted': '2026-09-20',
           'employmentType': 'FULL_TIME', 'description': '<p>Design county publications.</p>',
           'jobLocation': {'@type': 'Place', 'address': {'addressLocality': 'Towson, MD', 'addressRegion': 'MD', 'addressCountry': 'US'}}}
DETAIL_PAGE = '<html><script type="application/ld+json">' + json.dumps(POSTING) + '</script></html>'


class Http:
    def __init__(self):
        self.calls = []

    def get(self, url, params=None, accept=None, headers=None):
        self.calls.append((url, params, headers))
        if '/careers/home/index' in url:
            return Response(text=LIST_PAGE if (params or {}).get('page') == 1 else '<ul></ul>')
        return Response(text=DETAIL_PAGE)


class NeoGov(unittest.TestCase):
    def setUp(self):
        self.employer = Employer({'name': 'Baltimore County Government', 'system': 'neogov', 'agency': 'baltimorecounty',
                                  'domain': 'baltimorecountymd.gov'})

    def test_list_and_detail_become_an_opening(self):
        http = Http()
        found = self.employer.find_openings(['Graphic Designer'], http)
        self.assertEqual([opening['title'] for opening in found], ['Graphic Designer'])
        self.assertEqual((found[0]['location'], found[0]['posted']), ('Towson, MD, US', '2026-09-20'))
        self.assertEqual(found[0]['url'], 'https://www.governmentjobs.com/careers/baltimorecounty/jobs/501/graphic-designer')
        # The list endpoint answers only to an AJAX request.
        self.assertEqual(http.calls[0][2], {'X-Requested-With': 'XMLHttpRequest'})

    def test_closed_and_open_postings(self):
        url = 'https://www.governmentjobs.com/careers/baltimorecounty/jobs/501/graphic-designer'
        self.assertEqual(self.employer.status(url, Http()), 'Open')
        gone = SimpleNamespace(get=lambda *a, **k: Response(status=404))
        self.assertEqual(self.employer.status(url, gone), 'Closed')
        self.assertTrue(self.employer.adapter.owns(url))

    def test_agency_pages_are_recognised_in_web_results(self):
        self.assertEqual(identify('https://www.governmentjobs.com/careers/howardcountymd/jobs/123/x'),
                         {'system': 'neogov', 'agency': 'howardcountymd'})
        self.assertIsNone(identify('https://www.governmentjobs.com/careers/home'))


class DefaultList(unittest.TestCase):
    def test_new_local_employers_are_listed_once_each(self):
        names = [config['name'] for config in DEFAULT_EMPLOYERS]
        for wanted in ('Baltimore County Government', 'State of Delaware', 'City of Baltimore', 'M&T Bank', 'University of Maryland, College Park'):
            self.assertIn(wanted, names)
        self.assertEqual(len(names), len(set(names)))
        self.assertEqual(len({config_key(config) for config in DEFAULT_EMPLOYERS}), len(DEFAULT_EMPLOYERS))
        for config in DEFAULT_EMPLOYERS:
            Employer(config)  # every entry builds


class Internships(unittest.TestCase):
    def test_titles_and_work_types_that_mean_internship(self):
        self.assertTrue(shared.is_internship('2027 Summer Graphic Design & Creative Media Intern (Baltimore, MD)'))
        self.assertTrue(shared.is_internship('Content Designer', "['INTERN']"))
        self.assertTrue(shared.is_internship('Co-op Web Designer'))
        self.assertFalse(shared.is_internship('Internal Communications Designer'))
        self.assertFalse(shared.is_internship('International Brand Designer', "['FULL_TIME']"))

    def test_the_setting_turns_the_rule_off(self):
        with patch.object(shared, 'EXCLUDE_INTERNSHIPS', False):
            self.assertFalse(shared.is_internship('Design Intern'))

    def test_an_internship_is_skipped_in_a_search(self):
        skips, captured = [], []
        opening = dict(employer_opening(['Baltimore, MD, US']), title='Graphic Design Intern', schedule="['INTERN']")
        run_search({}, 'https://example.com/none', 'Graphic Designer', max_new=3,
                   extra={'load_employers': lambda: [FakeEmployer([opening])],
                          'save_company': lambda *a, **kw: captured.append(a) or True,
                          'record_skip': lambda *a: skips.append(a[0])})
        self.assertEqual(captured, [])
        self.assertIn('Internship', skips)

    def test_saved_rows_are_never_rejected_as_internships(self):
        self.assertTrue(relevance.is_internship_row({'is_kept': 0, 'career_job_title': 'Design Intern'}, {}))
        self.assertFalse(relevance.is_internship_row({'is_kept': 1, 'career_job_title': 'Design Intern'}, {}))


class CompanyBoards(unittest.TestCase):
    def setUp(self):
        from jobfinder.sources import ats_lookup
        ats_lookup.clear_cache()

    @staticmethod
    def ashby_http(slug, jobs):
        def get(url, params=None, accept=None, headers=None):
            if url == f'https://api.ashbyhq.com/posting-api/job-board/{slug}':
                return Response({'jobs': jobs})
            return Response(status=404)
        return SimpleNamespace(get=get, post_json=lambda *a, **k: Response(status=404))

    JOB = {'title': 'Visual Designer (AI-Native, Enterprise Focus)', 'location': 'United States', 'isRemote': True,
           'jobUrl': 'https://jobs.ashbyhq.com/aegis-ai/726f20fd-7a70-421c-93b2-87b6f54a688c?utm_source=x',
           'descriptionPlain': 'Design the product.', 'publishedAt': '2026-09-25T00:00:00Z'}

    def test_board_names_are_built_from_the_company_name(self):
        from jobfinder.sources.ats_lookup import slug_candidates
        self.assertEqual(slug_candidates('Aegis AI'), ['aegis-ai', 'aegisai'])
        self.assertEqual(slug_candidates('Acme Labs LLC'), ['acme-labs', 'acmelabs', 'acme'])

    def test_the_companys_own_ashby_posting_is_found_and_cleaned_of_tracking(self):
        from jobfinder.sources.ats_lookup import find_ats_posting
        found = find_ats_posting('Aegis AI', 'Visual Designer', self.ashby_http('aegis-ai', [self.JOB]))
        self.assertEqual(found['system'], 'ashby')
        self.assertEqual(found['url'], 'https://jobs.ashbyhq.com/aegis-ai/726f20fd-7a70-421c-93b2-87b6f54a688c')

    def test_another_job_at_the_board_is_not_mistaken_for_it(self):
        from jobfinder.sources.ats_lookup import find_ats_posting
        other = dict(self.JOB, title='Senior Backend Engineer')
        self.assertIsNone(find_ats_posting('Aegis AI', 'Visual Designer', self.ashby_http('aegis-ai', [other])))

    def test_a_company_with_no_board_returns_none(self):
        from jobfinder.sources.ats_lookup import find_ats_posting
        self.assertIsNone(find_ats_posting('Nobody Inc', 'Visual Designer', self.ashby_http('somewhere-else', [])))

    def test_a_search_uses_the_companys_own_posting_for_view(self):
        captured = []
        board = {'system': 'ashby', 'url': 'https://jobs.ashbyhq.com/acme/1', 'title': 'Web Designer', 'board': 'acme'}
        from test_search_flow import EmployerSites
        pages = {EmployerSites.JOB: __import__('test_search_flow').posting(EmployerSites.JOB, EmployerSites.TITLE, company='Acme')}
        run_search(pages, EmployerSites.JOB, 'Web Designer', max_new=1,
                   extra={'company_board_posting': lambda name, title: board,
                          'save_company': lambda *a, **kw: captured.append((a, kw)) or True})
        args, kwargs = captured[0]
        self.assertEqual(args[5], 'https://jobs.ashbyhq.com/acme/1')  # View link
        self.assertEqual(args[6], EmployerSites.JOB)                   # the source listing stays the source
        self.assertEqual(kwargs['listing_details']['ats_posting']['system'], 'ashby')
        self.assertGreaterEqual(args[3], 8)


class JobBoardQueries(unittest.TestCase):
    def queries(self, sites):
        debug = []
        with patch.object(shared, 'JOB_BOARD_SITES', sites):
            run_search({}, 'https://example.com/none', 'Web Designer, Visual Designer', max_new=1, debug_out=debug)
        return [item['query'] for item in debug[0]['runs'][-1]['queries']]

    def test_ashby_boards_are_searched_first_for_every_title(self):
        queries = self.queries(['jobs.ashbyhq.com'])
        self.assertEqual(queries[:2], ['site:jobs.ashbyhq.com "Web Designer"', 'site:jobs.ashbyhq.com "Visual Designer"'])

    def test_every_company_board_site_is_searched_for_each_title_with_few_pages(self):
        debug = []
        run_search({}, 'https://example.com/none', 'Web Designer', max_new=1, debug_out=debug)
        run = debug[0]['runs'][-1]
        site_queries = [item for item in run['queries'] if item['query'].startswith('site:')]
        # The typed title is searched on every board first (related O*NET titles follow).
        self.assertEqual([item['query'] for item in site_queries[:5]],
                         [f'site:{site} "Web Designer"' for site in
                          ('jobs.ashbyhq.com', 'greenhouse.io', 'jobs.lever.co', 'apply.workable.com', 'jobs.smartrecruiters.com')])

    def test_the_site_list_can_be_emptied(self):
        self.assertFalse(any(query.startswith('site:') for query in self.queries([])))


class SitemapLookup(unittest.TestCase):
    SITEMAP = ('<urlset><url><loc>https://acme.example/about</loc></url>'
               '<url><loc>https://acme.example/jobs/senior-graphic-designer-baltimore</loc></url>'
               '<url><loc>https://acme.example/jobs/web-developer</loc></url></urlset>')
    JOB_PAGE = '<html><head><title>Senior Graphic Designer | Acme</title></head><body><h1>Senior Graphic Designer</h1></body></html>'

    def pages(self, raw):
        def fetch_raw(url):
            return SimpleNamespace(url=url, text=raw[url]) if url in raw else None

        def fetch(url):
            return SimpleNamespace(url=url, text=self.JOB_PAGE) if 'senior-graphic-designer' in url else None
        return fetch, fetch_raw

    def test_an_opening_is_found_through_the_sitemap_and_checked_against_its_page(self):
        from jobfinder.sources import employer_site
        employer_site.clear_cache()
        fetch, fetch_raw = self.pages({'https://acme.example/sitemap.xml': self.SITEMAP})
        found = employer_site._find_in_sitemap('acme.example', 'Senior Graphic Designer', fetch=fetch, fetch_raw=fetch_raw)
        self.assertEqual(found, 'https://acme.example/jobs/senior-graphic-designer-baltimore')

    def test_nested_sitemaps_and_robots_files_are_followed(self):
        from jobfinder.sources import employer_site
        employer_site.clear_cache()
        index = '<sitemapindex><sitemap><loc>https://acme.example/job-sitemap.xml</loc></sitemap></sitemapindex>'
        fetch, fetch_raw = self.pages({'https://acme.example/robots.txt': 'Sitemap: https://acme.example/sitemap_main.xml',
                                       'https://acme.example/sitemap_main.xml': index,
                                       'https://acme.example/job-sitemap.xml': self.SITEMAP})
        self.assertIsNotNone(employer_site._find_in_sitemap('acme.example', 'Graphic Designer', fetch=fetch, fetch_raw=fetch_raw))

    def test_a_page_that_does_not_name_the_job_is_not_accepted(self):
        from jobfinder.sources import employer_site
        employer_site.clear_cache()
        fetch_raw = lambda url: SimpleNamespace(url=url, text=self.SITEMAP) if url.endswith('/sitemap.xml') else None
        wrong_page = lambda url: SimpleNamespace(url=url, text='<title>Careers | Acme</title><h1>Join us</h1>')
        self.assertIsNone(employer_site._find_in_sitemap('acme.example', 'Senior Graphic Designer', fetch=wrong_page, fetch_raw=fetch_raw))

    def test_no_sitemap_means_no_result(self):
        from jobfinder.sources import employer_site
        employer_site.clear_cache()
        self.assertIsNone(employer_site._find_in_sitemap('acme.example', 'Graphic Designer', fetch=lambda u: None, fetch_raw=lambda u: None))


class SubdomainSites(unittest.TestCase):
    def setUp(self):
        from jobfinder.sources import employer_site
        employer_site.clear_cache()

    def test_a_listing_on_a_subdomain_of_the_employers_site_shows_that_subdomain(self):
        from jobfinder.sources import employer_site
        posting = 'https://corcoran.gwu.edu/web-designer-university-maryland'
        pages = {'https://www.gwu.edu/': '<title>The George Washington University | GW</title><a href="/careers">Careers</a>',
                 'https://www.gwu.edu/careers': '<title>Careers | GW</title><h1>Careers</h1>'}
        fetch = lambda url: SimpleNamespace(url=url, text=pages[url]) if url in pages else None
        html = '<a href="https://www.gwu.edu/">Company website</a>'
        site = employer_site.resolve_employer_site('George Washington University', 'Web Designer', posting, html, ['Web Designer'],
                                                   fetch=fetch, score_page=lambda u, t: {'score': 0})
        self.assertEqual((site['domain'], site['posting_url']), ('corcoran.gwu.edu', posting))
        self.assertTrue(any('own site' in line for line in site['evidence']))

    def test_the_label_for_a_listing_on_the_companys_own_subdomain(self):
        label = company_names.verification_label({'employer_site': {'domain': 'corcoran.gwu.edu', 'posting_found': True}}, None,
                                          'Corcoran School', 'https://corcoran.gwu.edu/web-designer')
        self.assertEqual(label, "Posted on the company's own site")


class Verification(unittest.TestCase):
    def label(self, details=None, source_type=None, source='https://jobs.example/board/1', name='Acme Design'):
        return company_names.verification_label(details or {}, source_type, name, source)

    def test_each_case_is_said_plainly(self):
        self.assertEqual(self.label(source_type='Employer careers'), "Company's own careers site")
        self.assertIn('Ashby', self.label({'ats_posting': {'system': 'ashby'}}))
        self.assertEqual(self.label({'employer_site': {'posting_found': True}}), "Listed on the company's website")
        self.assertIn('not listed there', self.label({'employer_site': {'posting_found': False}}))
        self.assertEqual(self.label(source='https://www.acmedesign.com/jobs/1'), "Posted on the company's own site")
        self.assertIn('not verified', self.label())


class TitleCoverage(unittest.TestCase):
    TYPED = ['content designer', 'graphic design', 'production specalist', 'Visual Designer', 'web designer', 'Web Producer']

    def test_misspelled_words_are_corrected_and_correct_ones_left_alone(self):
        from jobfinder.profiles.onet_data import spelling_fix
        self.assertEqual(spelling_fix('production specalist'), 'production specialist')
        self.assertEqual(spelling_fix('Sr. Graphic Desginer'), 'Sr. Graphic Designer')
        for fine in ('Web Producer', 'UX Researcher', 'Kubernetes Engineer', 'graphic design'):
            self.assertEqual(spelling_fix(fine), fine)

    def test_related_titles_are_matched_but_only_for_the_typed_families(self):
        extras = shared.related_family_titles(self.TYPED)
        for wanted in ('Multimedia Designer', 'Production Artist', 'Website Producer', 'UX Writer'):
            self.assertIn(wanted, extras)
        self.assertEqual(shared.related_family_titles(['Accountant']), [])
        self.assertTrue(finder.matching_job_title('Brand Designer', self.TYPED + extras))
        self.assertFalse(finder.matching_job_title('Brand Accountant', self.TYPED + extras))

    def test_related_titles_can_be_switched_off(self):
        with patch.object(shared, 'RELATED_TITLES', False):
            self.assertEqual(shared.related_family_titles(self.TYPED), [])

    def test_a_search_corrects_the_spelling_and_does_not_query_the_related_families(self):
        debug = []
        run_search({}, 'https://example.com/none', 'production specalist, web designer', max_new=1, debug_out=debug)
        inputs = debug[0]['runs'][-1]['inputs']
        self.assertEqual(inputs['spelling_corrections'], [{'typed': 'production specalist', 'corrected': 'production specialist'}])
        self.assertIn('Multimedia Designer', inputs['related_family_titles'])
        queries = ' '.join(item['query'] for item in debug[0]['runs'][-1]['queries'])
        self.assertNotIn('Multimedia Designer', queries)
        self.assertIn('production specialist', queries)


if __name__ == '__main__':
    unittest.main()
