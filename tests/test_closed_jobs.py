import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import no_database  # noqa: F401  (cuts tests off from the real database)
from closed_jobs import apply_targets, check_apply_target, listing_closed

PAGE = 'https://www.mediabistro.com/jobs/3542960377-graphic-designer-iii'
REDIRECT = 'https://jobs-us.disruptedcloud.com/redirectfeedjob?jobid=4768060D'
EXPIRED = 'https://jobs-us.disruptedcloud.com/SimilarExpiredJob?jobid=4768060D&source=attb'
NOTICE = 'https://jobs-us.disruptedcloud.com/SimilarExpiredJobWB?jobid=4768060D&source=attb'
# The button has no link of its own; its address sits in the page's script data, escaped.
MEDIABISTRO = ('<html><body><button>Save Job</button><button>Apply on Company Site</button>'
               '<script>{"applyLink":"https:\\/\\/jobs-us.disruptedcloud.com\\/redirectfeedjob?jobid=4768060D",'
               '"logo":"https:\\/\\/production.s3.amazonaws.com\\/logo.png",'
               '"sentry":"https:\\/\\/abc@o1.ingest.sentry.io\\/1"}</script></body></html>')


def response(status=200, location=None, text='', ):
    return SimpleNamespace(status_code=status, headers={'Location': location} if location else {}, text=text)


def chain(routes):
    calls = []
    def get(url):
        calls.append(url)
        return routes.get(url)
    get.calls = calls
    return get


class ClosedJobs(unittest.TestCase):
    def test_a_button_with_its_address_in_script_data_is_found(self):
        self.assertEqual(apply_targets(PAGE, MEDIABISTRO), [REDIRECT])

    def test_apply_links_are_found_and_trackers_are_ignored(self):
        html = ('<a href="https://careers.example.org/job/1">Apply Now</a><a href="/about">About</a>'
                '<a href="https://www.googletagmanager.com/x?apply=1">Apply</a>')
        self.assertEqual(apply_targets(PAGE, html), ['https://careers.example.org/job/1'])

    def test_the_mediabistro_chain_ends_at_an_expired_notice(self):
        get = chain({REDIRECT: response(302, EXPIRED), EXPIRED: response(302, NOTICE),
                     NOTICE: response(200, text='<title>Expired - Similar Jobs</title>We are sorry to say that the job is no longer available.')})
        result = listing_closed(PAGE, MEDIABISTRO, get)
        self.assertTrue(result['closed'])
        self.assertIn('expired', result['reason'])
        self.assertEqual(result['apply_link'], REDIRECT)

    def test_a_live_employer_posting_is_not_closed(self):
        get = chain({REDIRECT: response(302, 'https://jobs.example.org/posting/1'),
                     'https://jobs.example.org/posting/1': response(200, text='<title>Graphic Designer</title><p>Apply today.</p>')})
        self.assertIsNone(listing_closed(PAGE, MEDIABISTRO, get))

    def test_not_found_and_gone_mean_closed(self):
        for status in (404, 410):
            get = chain({REDIRECT: response(status)})
            self.assertTrue(listing_closed(PAGE, MEDIABISTRO, get)['closed'], status)

    def test_page_wording_that_says_the_job_is_filled(self):
        get = chain({'https://jobs.example.org/1': response(200, text='<h1>Sorry</h1><p>This position has been filled.</p>')})
        self.assertTrue(check_apply_target('https://jobs.example.org/1', get)['closed'])

    def test_an_unreachable_target_is_left_undecided(self):
        get = chain({})
        self.assertIsNone(listing_closed(PAGE, MEDIABISTRO, get))
        self.assertIsNone(check_apply_target(REDIRECT, get))

    def test_a_redirect_loop_gives_up(self):
        get = chain({REDIRECT: response(302, REDIRECT)})
        self.assertIsNone(check_apply_target(REDIRECT, get))
        self.assertLessEqual(len(get.calls), 7)

    def test_a_page_without_an_apply_button_has_no_targets(self):
        self.assertEqual(apply_targets(PAGE, '<p>Just text</p>'), [])


if __name__ == '__main__':
    unittest.main()
