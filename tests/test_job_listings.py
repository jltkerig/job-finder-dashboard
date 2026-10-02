import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import no_database  # noqa: F401  (cuts tests off from the real database)
from jobfinder.sources.job_listings import canonical_url, excludes_us, extract_jobs, is_pdf_url, job_links
from jobfinder.sources.ats_feeds import public_board_links


class ListingChecks(unittest.TestCase):
    def test_article_and_service_pages_do_not_become_jobs(self):
        article = '<meta property="og:type" content="article"><h1>UMSOM Student Is on a Critical Mission</h1><a href="/careers">Careers</a>'
        service = '<h1>Modern Maryland Website Design Company</h1><a href="/careers">Careers</a>'
        guide = '<h1>Campus Employment & Internships</h1><a href="/apply">Apply</a>'
        self.assertEqual(extract_jobs('https://catalystmag.umaryland.edu/news/student', article, ['Web Designer']), [])
        self.assertEqual(extract_jobs('https://janbaskdigitaldesign.com/maryland-website-design-services', service, ['Web Designer']), [])
        self.assertEqual(extract_jobs('https://financialaid.umbc.edu/types-of-aid/employment/campus', guide, ['Web Designer']), [])

    def test_two_distinct_jobs_same_employer(self):
        html = '''<script type="application/ld+json">{"@graph":[
          {"@type":"JobPosting","title":"Web Designer","url":"https://example.com/jobs/100","hiringOrganization":{"name":"Example"}},
          {"@type":"JobPosting","title":"Frontend Developer","url":"https://example.com/jobs/200","hiringOrganization":{"name":"Example"}}
        ]}</script>'''
        found = extract_jobs('https://example.com/careers', html, ['Web Designer', 'Front End Developer'])
        self.assertEqual(len(found), 2)
        self.assertNotEqual(found[0]['url'], found[1]['url'])

    def test_job_details_and_expiry(self):
        html = '''<script type="application/ld+json">{"@type":"JobPosting","title":"Web Designer",
          "url":"https://example.com/jobs/100?utm_source=search","employmentType":"PART_TIME",
          "datePosted":"2026-09-20","baseSalary":{"currency":"USD","value":{"minValue":30,"maxValue":40,"unitText":"HOUR"}}}</script>'''
        found = extract_jobs('https://example.com/careers', html, ['Web Designer'])
        self.assertEqual(found[0]['url'], 'https://example.com/jobs/100')
        self.assertEqual(found[0]['schedule'], 'PART_TIME')
        self.assertEqual(found[0]['salary'], '$30–$40 / hour')
        self.assertEqual(extract_jobs('https://example.com/jobs/old', html.replace('"datePosted"', '"validThrough"').replace('2026-09-20', '2020-01-01'), ['Web Designer']), [])

    def test_job_links_and_location_restrictions(self):
        links = job_links('https://example.com/careers', '<a href="/jobs/100">Web Designer</a><a href="/jobs/200">Frontend Developer</a>')
        self.assertEqual(len(links), 2)
        self.assertTrue(excludes_us('Remote - Germany'))
        self.assertFalse(excludes_us('Remote - United States'))
        self.assertEqual(canonical_url('https://example.com/jobs/100/?utm_source=x'), 'https://example.com/jobs/100')

    def test_public_board_returns_only_matching_openings(self):
        class Response:
            def raise_for_status(self): pass
            def json(self): return [{'text': 'Web Designer', 'hostedUrl': 'https://jobs.lever.co/example/1'},
                                    {'text': 'Accountant', 'hostedUrl': 'https://jobs.lever.co/example/2'}]
        with patch('jobfinder.sources.ats_feeds.requests.get', return_value=Response()):
            self.assertEqual(public_board_links('https://jobs.lever.co/example', ['Web Designer']),
                             ['https://jobs.lever.co/example/1'])

    def test_a_job_page_marked_as_an_article_is_kept_when_it_has_a_job_posting(self):
        posting = '<script type="application/ld+json">' + json.dumps({
            '@type': 'JobPosting', 'title': 'Web Designer', 'url': 'https://careers.example.com/job/1/web-designer',
            'hiringOrganization': {'name': 'Example'}}) + '</script>'
        marked = '<meta property="og:type" content="article">'
        jobs = extract_jobs('https://careers.example.com/job/1/web-designer', marked + posting, ['Web Designer'])
        self.assertEqual([job['title'] for job in jobs], ['Web Designer'])
        # ...but a real article without a posting is still not a job.
        self.assertEqual(extract_jobs('https://example.com/news/x', marked + '<h1>Web Designer</h1><a href="/apply">Apply</a>',
                                      ['Web Designer']), [])

    def test_us_abbreviation_with_periods_counts_as_us(self):
        description = 'Candidates must be based in Canada or the U.S.'
        self.assertFalse(excludes_us('Remote, U.S.', description))
        self.assertFalse(excludes_us('U.S.', description))
        self.assertTrue(excludes_us('Toronto, Canada', description))

    def test_pdf_links_are_ignored(self):
        self.assertTrue(is_pdf_url('https://example.com/careers/Job-Description.PDF?v=2'))
        self.assertFalse(is_pdf_url('https://example.com/jobs/pdf-designer'))
        html = '<a href="/jobs/web-designer.pdf">Web Designer</a><a href="/jobs/web-designer">Web Designer</a>'
        self.assertEqual(job_links('https://example.com/careers', html), ['https://example.com/jobs/web-designer'])


if __name__ == '__main__':
    unittest.main()
