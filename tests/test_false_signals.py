"""Regression tests for things a job page can say that look like evidence but are not."""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import job_finder as finder
from bs4 import BeautifulSoup
from employer_site import _names_match
from job_listings import NON_JOB_PATH, excludes_us, extract_jobs, matching_title
from profile_tools import listing_skills
from remote_ok import matching_jobs


def arrangement(title, description, location='Austin, TX'):
    data = {'@type': 'JobPosting', 'title': title, 'url': 'https://example.com/jobs/1', 'description': description,
            'jobLocation': {'address': {'addressLocality': location.split(',')[0], 'addressRegion': 'TX'}},
            'hiringOrganization': {'name': 'Example'}}
    html = '<script type="application/ld+json">' + json.dumps(data) + '</script>'
    return extract_jobs('https://example.com/jobs/1', html, [title])[0]['type']


class RemoteAndOnsiteWording(unittest.TestCase):
    def test_1_not_remote_is_not_remote(self):
        self.assertEqual(arrangement('Web Designer', 'This is not a remote position. Onsite in Austin.'), 'Onsite')
        self.assertEqual(arrangement('Web Designer', 'No remote work available for this role.'), 'Onsite')
        self.assertEqual(arrangement('Web Designer', 'This is a non-remote role.'), 'Onsite')
        self.assertEqual(finder.detect_work_arrangement('Web Designer (Not Remote)'), 'Onsite')

    def test_1_remote_perks_are_not_remote_jobs(self):
        self.assertEqual(arrangement('Web Designer', 'We offer remote work stipends. The role is in office in Austin.'), 'Onsite')
        self.assertIsNone(arrangement('Web Designer', 'Great remote work policy and equipment budget.'))

    def test_1_real_remote_wording_still_works(self):
        self.assertEqual(arrangement('Web Designer', 'This is a fully remote position.'), 'Remote')
        self.assertEqual(arrangement('Web Designer', 'The role is remote-first with a home office stipend.'), 'Remote')
        self.assertEqual(arrangement('Remote Web Designer', 'Join us.'), 'Remote')
        self.assertEqual(arrangement('Web Designer', 'Two days remote each week, hybrid schedule.'), 'Hybrid')

    def test_2_remote_as_a_subject_is_not_a_location(self):
        self.assertIsNone(finder.detect_work_arrangement('Remote Sensing Web Designer'))
        self.assertIsNone(arrangement('Remote Monitoring Web Designer', 'Build dashboards.'))
        self.assertEqual(finder.detect_work_arrangement('Remote Web Designer'), 'Remote')

    def test_14_an_in_person_interview_does_not_make_a_job_onsite(self):
        self.assertIsNone(arrangement('Web Designer', 'Finalists attend an in-person interview.'))
        self.assertIsNone(arrangement('Web Designer', 'Interviews are conducted on-site at our studio. Quarterly in-person team events.'))
        self.assertEqual(arrangement('Web Designer', 'This is an in-person role at our Austin office.'), 'Onsite')


class LocationEvidence(unittest.TestCase):
    def place(self, html, extra=''):
        result = finder.analyze_usa_location(html, extra_text=extra)
        return result['state'], result['score']

    def test_3_boilerplate_and_names_do_not_set_the_state(self):
        for html in ('<p>Web Designer in Berlin.</p><p>Acme Inc, a Delaware corporation.</p>',
                     '<p>Web Designer in Berlin.</p><p>See our California Consumer Privacy Act notice.</p>',
                     '<p>Web Designer. As seen in the Washington Post.</p>',
                     '<p>Web Designer. Team led by Jane Doe, MD.</p>',
                     '<p>Web Designer. Contact John Smith, PA.</p>',
                     '<p>Web Designer. Fans of Indiana Jones.</p>'):
            self.assertEqual(self.place(html), (None, 0), html)

    def test_3_footers_sidebars_and_office_lists_are_ignored(self):
        html = ('<main><p>Web Designer in London.</p><p>Offices: London, New York, United States</p></main>'
                '<footer>Denver, CO 80202. United States</footer>'
                '<div class="related-jobs">Designer in Austin, TX 78701</div>')
        self.assertEqual(self.place(html), (None, 0))

    def test_3_real_locations_still_count(self):
        self.assertEqual(self.place('<p>Our office: Atlanta, Georgia</p>')[0], 'GA')
        self.assertEqual(self.place('<p>Location: Towson, MD</p>')[0], 'MD')
        self.assertEqual(self.place('<p>Towson, MD 21204</p>')[0], 'MD')
        self.assertEqual(self.place('<p>Web Designer</p>', extra='Towson, MD, USA')[0], 'MD')

    def test_a_listing_location_ending_in_us_counts_as_the_country(self):
        self.assertEqual(self.place('<p>Web Designer</p>', extra='Baltimore, MD, US'), ('MD', 7))
        self.assertEqual(self.place('<p>Web Designer</p>', extra='Remote, U.S.'), (None, 4))
        self.assertEqual(self.place('<p>Join us today. Contact us.</p>', extra='Baltimore, MD'), ('MD', 3))
        self.assertEqual(self.place('<p>Made by US customers</p>'), (None, 0))

    def test_5_a_salary_is_not_a_zip_code(self):
        self.assertEqual(self.place('<p>Web Designer. Salary $90000.</p>'), (None, 0))
        self.assertEqual(self.place('<p>Web Designer. Towson, MD 21204</p>'), ('MD', 4))

    def test_6_the_city_guess_skips_names_and_prefers_location_wording(self):
        page = '<p>Contact Jane Doe, MD for details. Location: Towson, MD</p>'
        self.assertEqual(finder.extract_job_city(page), ('Towson', 'MD'))
        self.assertEqual(finder.extract_job_city('<p>Email Jane Doe, MD</p><p>Baltimore, MD 21201</p>'), ('Baltimore', 'MD'))
        self.assertEqual(finder.extract_job_city('<p>Anything</p>', fallback_text='Towson, MD, USA'), ('Towson', 'MD'))


class UsOnlyRejection(unittest.TestCase):
    def test_4_a_country_mentioned_later_does_not_reject_a_us_job(self):
        self.assertFalse(excludes_us('New York, NY', 'Applicants must be based in New York and support clients in Mexico.'))
        self.assertFalse(excludes_us('Remote', 'Candidates based in the US will work closely with teams in Europe.'))

    def test_4_a_real_country_requirement_still_rejects(self):
        self.assertTrue(excludes_us('Remote', 'Candidates must be based in Canada.'))
        self.assertTrue(excludes_us('Remote', 'Open to residents of the United Kingdom only.'))
        self.assertTrue(excludes_us('Remote', 'Applicants located in Germany only.'))
        self.assertTrue(excludes_us('Remote (UK only)', ''))


class Skills(unittest.TestCase):
    def test_8_everyday_words_are_not_skills(self):
        text = ('You will react quickly to feedback. We have a notion of quality and a confluence of ideas. '
                'Bootstrap the brand. Litmus test for new ideas.')
        self.assertEqual(listing_skills('<p>' + text + '</p>'), [])

    def test_8_tools_named_like_tools_still_count(self):
        text = ('Experience with React, Vue and Figma. Proficiency in Notion. We use Jira, Confluence and Bootstrap. '
                'Email QA in Litmus.')
        self.assertEqual(set(listing_skills('<p>' + text + '</p>')),
                         {'React', 'Vue', 'Figma', 'Notion', 'Jira', 'Confluence', 'Bootstrap', 'Litmus'})


class TitleMatching(unittest.TestCase):
    def test_9_extra_words_that_change_the_job_do_not_match(self):
        for wanted, actual in (('Web Producer', 'Web Series Producer'), ('Visual Designer', 'Visual Merchandising Designer'),
                               ('Web Designer', 'Web Application Security Designer'),
                               ('Graphic Design', 'Interior Graphic Design Manager')):
            self.assertFalse(matching_title(actual, [wanted]), actual)

    def test_9_a_department_in_brackets_is_not_part_of_the_job(self):
        self.assertTrue(matching_title('Lead, Digital Designer (Apparel & Footwear) - Quick-to-Market', ['Digital Designer']))
        self.assertFalse(matching_title('Apparel Designer', ['Web Designer']))
        self.assertFalse(matching_title('Fashion Designer - Digital', ['Digital Designer']))
        self.assertFalse(matching_title('Interior Designer (Web Team)', ['Web Designer']))

    def test_9_normal_variations_still_match(self):
        for wanted, actual in (('Web Designer', 'Senior Web Designer II'), ('Web Designer', 'UX-Driven Web Designer & Front-End Specialist'),
                               ('Web Designer', 'Web & UI Designer'), ('Front End Developer', 'Frontend Developer'),
                               ('Content Designer', 'Staff Content Designer, Investing'), ('Web Designer', 'Web Designer Intern')):
            self.assertTrue(matching_title(actual, [wanted]), actual)

    def test_9_remote_ok_uses_the_same_rule(self):
        jobs = [{'position': 'Web Series Producer', 'location': 'Worldwide', 'url': 'https://remoteok.com/1', 'description': ''},
                {'position': 'Web Producer', 'location': 'Worldwide', 'url': 'https://remoteok.com/2', 'description': ''}]
        self.assertEqual([job['title'] for job in matching_jobs(jobs, ['Web Producer'])], ['Web Producer'])


class EmployerNameCheck(unittest.TestCase):
    def test_10_the_name_must_be_a_whole_word(self):
        self.assertFalse(_names_match('Wise', 'Otherwise Solutions'))
        self.assertFalse(_names_match('Core', 'Hardcore Fitness'))

    def test_10_real_matches_still_pass(self):
        self.assertTrue(_names_match('Wise', 'Wise | Money without borders'))
        self.assertTrue(_names_match('YOUCANIC', 'YOUCANIC'))
        self.assertTrue(_names_match('Wealthsimple Technologies', 'Wealthsimple - Investing'))
        self.assertTrue(_names_match('Johns Hopkins University', 'Johns Hopkins University'))


class CompanyNames(unittest.TestCase):
    def name(self, title, search_title=''):
        return finder.extract_company_name(BeautifulSoup(f'<title>{title}</title>', 'html.parser'), search_title, 'acme.com')

    def test_11_a_job_title_is_not_the_company(self):
        self.assertEqual(self.name('Senior Web Designer | Acme'), 'Acme')
        self.assertEqual(self.name('Web Designer at Acme Corp'), 'Acme Corp')
        self.assertEqual(self.name('Web Designer - Acme - Careers'), 'Acme')
        self.assertEqual(self.name('Senior Web Designer', 'Acme Labs | Jobs'), 'Acme Labs')

    def test_11_ordinary_titles_are_unchanged(self):
        self.assertEqual(self.name('Acme Careers | Join our team'), 'Acme Careers')


class NotAJobFilters(unittest.TestCase):
    def test_12_job_titles_with_reviews_or_find_a_are_not_directories(self):
        self.assertFalse(finder.is_directory_or_marketplace_result('example.com', 'Product Reviews Content Designer'))
        self.assertFalse(finder.is_directory_or_marketplace_result('example.com', 'Find a Career in Design'))

    def test_12_real_directories_are_still_caught(self):
        for title in ('Best Web Design Agencies in Maryland', 'Top 10 Web Designers', 'Find a Web Designer near you',
                      'Web Design Reviews and Ratings', 'Compare Providers'):
            self.assertTrue(finder.is_directory_or_marketplace_result('example.com', title), title)

    def test_13_a_services_folder_inside_careers_holds_real_jobs(self):
        self.assertFalse(NON_JOB_PATH.search('/careers/services/web-designer'))
        self.assertFalse(NON_JOB_PATH.search('/jobs/services/1234'))

    def test_13_service_pages_and_articles_are_still_not_jobs(self):
        for path in ('/services/web-design', '/our-services', '/news/2026/hiring', '/blog/careers-advice', '/work-study/jobs'):
            expected = path != '/our-services'
            self.assertEqual(bool(NON_JOB_PATH.search(path)), expected, path)


if __name__ == '__main__':
    unittest.main()
