import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import no_database  # noqa: F401  (cuts tests off from the real database)
import employer_site
from employer_site import can_guess_domain, host_matches_company, is_third_party, resolve_employer_site

POSTING = 'https://www.jobleads.com/us/job/ux-driven-web-designer-front-end-specialist--towson--e9c63'
LISTING_HTML = '<html><body><h1>UX-Driven Web Designer</h1><p>YOUCANIC is hiring.</p></body></html>'
HOME = '<meta property="og:site_name" content="YOUCANIC"><title>YOUCANIC | Design</title><a href="/careers/">Careers</a>'
CAREERS = '<title>Careers | YOUCANIC</title><h1>Careers</h1><p>Contact us to join the team.</p>'


def fetcher(pages, calls=None):
    def fetch(url):
        if calls is not None:
            calls.append(url)
        return SimpleNamespace(url=url, text=pages[url]) if url in pages else None
    return fetch


def resolve(company='YOUCANIC', pages=None, html=LISTING_HTML, posting=POSTING, calls=None, notes=None, **kw):
    return resolve_employer_site(company, 'UX-Driven Web Designer & Front-End Specialist', posting, html,
                                 ['web designer'], fetch=fetcher(pages or {}, calls), notes=notes,
                                 score_page=lambda url, text: {'score': 0}, **kw)


class EmployerSite(unittest.TestCase):
    def setUp(self):
        employer_site.clear_cache()

    def test_youcanic_is_found_from_a_jobleads_listing(self):
        pages = {'https://www.youcanic.com/': HOME, 'https://www.youcanic.com/careers/': CAREERS}
        result = resolve(pages=pages)
        self.assertEqual(result['careers_url'], 'https://www.youcanic.com/careers/')
        self.assertEqual(result['domain'], 'youcanic.com')
        self.assertEqual(result['method'], 'domain guess')
        self.assertIsNone(result['posting_url'])

    def test_a_guessed_site_that_is_another_company_is_rejected(self):
        notes = []
        pages = {'https://www.youcanic.com/': '<title>Some Other Studio</title><a href="/careers/">Careers</a>',
                 'https://www.youcanic.com/careers/': CAREERS}
        self.assertIsNone(resolve(pages=pages, notes=notes))
        self.assertTrue(any('does not name' in note for note in notes))

    def test_listing_already_on_the_employer_domain_is_left_alone(self):
        self.assertFalse(is_third_party('https://www.youcanic.com/jobs/1', 'YOUCANIC'))
        self.assertIsNone(resolve(posting='https://www.youcanic.com/jobs/1'))
        self.assertTrue(is_third_party(POSTING, 'YOUCANIC'))

    def test_hiring_organization_link_is_used_first(self):
        html = '<script type="application/ld+json">' + json.dumps({
            '@type': 'JobPosting', 'title': 'Web Designer',
            'hiringOrganization': {'name': 'Acme Robotics', 'sameAs': 'https://acme-robots.io/'}}) + '</script>'
        pages = {'https://acme-robots.io/': '<a href="/careers">Careers</a>',
                 'https://acme-robots.io/careers': '<h1>Careers</h1>'}
        result = resolve('Acme Robotics', pages, html=html)
        self.assertEqual(result['method'], 'listing data (hiringOrganization)')
        self.assertEqual(result['careers_url'], 'https://acme-robots.io/careers')

    def test_excluded_hosts_are_never_used(self):
        html = '<script type="application/ld+json">' + json.dumps({
            '@type': 'JobPosting', 'hiringOrganization': {'sameAs': 'https://www.linkedin.com/company/acme'}}) + '</script>'
        calls = []
        result = resolve('Acme Robotics', {}, html=html, calls=calls, is_excluded=lambda host: 'linkedin' in host)
        self.assertIsNone(result)
        self.assertFalse(any('linkedin' in url for url in calls))

    def test_site_without_a_careers_page_is_still_named_as_the_employer(self):
        notes = []
        site = resolve(pages={'https://www.youcanic.com/': HOME}, notes=notes)
        self.assertTrue(any('no careers page' in note for note in notes))
        self.assertEqual((site['domain'], site['careers_url'], site['posting_url']), ('youcanic.com', None, None))
        self.assertTrue(any('apply through the listing' in line for line in site['evidence']))

    def test_generic_trailing_words_are_dropped_when_guessing_the_domain(self):
        home = '<html><head><title>Wholesale Supplies Online | SZCO</title></head><body><h1>SZCO</h1></body></html>'
        site = resolve('Szco Supplies Inc', {'https://szco.com/': home})
        self.assertEqual(site['domain'], 'szco.com')

    def test_department_names_do_not_trigger_domain_guesses(self):
        calls = []
        self.assertIsNone(resolve('Office of Human Resources', {}, calls=calls))
        self.assertEqual(calls, [])
        self.assertFalse(can_guess_domain('Office of Human Resources'))
        self.assertFalse(can_guess_domain('IBM'))
        self.assertTrue(can_guess_domain('YOUCANIC'))

    def test_the_exact_opening_on_the_employer_site_is_preferred(self):
        opening = 'https://www.youcanic.com/careers/ux-driven-web-designer'
        careers = CAREERS + '<script type="application/ld+json">' + json.dumps({
            '@type': 'JobPosting', 'title': 'UX-Driven Web Designer & Front-End Specialist', 'url': opening}) + '</script>'
        pages = {'https://www.youcanic.com/': HOME, 'https://www.youcanic.com/careers/': careers}
        result = resolve(pages=pages)
        self.assertEqual(result['posting_url'], opening)
        self.assertIn("this opening found on the employer's site", result['evidence'])

    def test_search_results_are_used_only_when_search_is_allowed(self):
        pages = {'https://www.acmerobots.com/': '<title>Acme Robotics</title><a href="/careers/">Careers</a>',
                 'https://www.acmerobots.com/careers/': '<h1>Careers</h1>'}
        search = lambda query: [{'url': 'https://www.linkedin.com/company/acme'}, {'url': 'https://www.acmerobots.com/about'}]
        result = resolve('Acme Robotics', pages, search=search, is_excluded=lambda host: 'linkedin' in host)
        self.assertEqual(result['method'], 'web search')
        employer_site.clear_cache()
        self.assertIsNone(resolve('Acme Robotics', pages, search=search, allow_search=False))

    def test_a_university_is_found_by_its_initials_and_its_jobs_subdomain_is_used(self):
        pages = {'https://www.jhu.edu/': '<meta property="og:site_name" content="Johns Hopkins University">'
                                         '<a href="https://jobs.jhu.edu/">Jobs at Hopkins</a>',
                 'https://jobs.jhu.edu/': '<title>Johns Hopkins Careers</title><h1>Search jobs</h1>'}
        result = resolve('Johns Hopkins University', pages)
        self.assertEqual((result['domain'], result['careers_url'], result['method']),
                         ('jhu.edu', 'https://jobs.jhu.edu/', 'school domain guess'))

    def test_school_domain_guesses(self):
        self.assertEqual(employer_site.school_slugs('Johns Hopkins University'), ['jhu', 'johnshopkins'])
        self.assertEqual(employer_site.school_slugs('University of Maryland, Baltimore County'),
                         ['umbc', 'marylandbaltimorecounty'])
        self.assertEqual(employer_site.school_slugs('Acme Robotics'), [])

    def test_subdomains_count_as_the_same_site(self):
        self.assertTrue(employer_site.same_site('jobs.jhu.edu', 'www.jhu.edu'))
        self.assertFalse(employer_site.same_site('notjhu.edu', 'jhu.edu'))

    def test_name_matching_helpers(self):
        self.assertTrue(host_matches_company('www.youcanic.com', 'YOUCANIC'))
        self.assertTrue(host_matches_company('zebra.com', 'Zebra Technologies, Inc.'))
        self.assertFalse(host_matches_company('jobleads.com', 'YOUCANIC'))
        self.assertTrue(host_matches_company('acme.co.uk', 'Acme Ltd'))


if __name__ == '__main__':
    unittest.main()
