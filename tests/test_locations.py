from jobfinder.search import usa_location
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import no_database  # noqa: F401  (cuts tests off from the real database)
sys.path.insert(0, str(Path(__file__).resolve().parent))
import job_finder as finder
from jobfinder.search import company_names
from jobfinder.sources.job_listings import excludes_us
from test_search_flow import posting, run_search


class NonUsLocations(unittest.TestCase):
    def test_country_codes_and_names_are_not_us(self):
        for location in ('London, GB', 'Paris, FR', 'Dublin, IE', 'Toronto, Canada', 'Berlin, Germany', 'Remote, UK'):
            self.assertTrue(excludes_us(location), location)

    def test_us_places_and_state_codes_that_look_like_countries_are_kept(self):
        for location in ('London, GA', 'Wilmington, DE', 'Remote, U.S.', 'Towson, Maryland, US', 'Paris, TX', 'Remote'):
            self.assertFalse(excludes_us(location), location)


class PlaceMatching(unittest.TestCase):
    def test_a_road_or_lookalike_is_not_the_place(self):
        self.assertFalse(finder.place_matches('london, md, de', 'New London Road, Thabar, Cecil County, Maryland, United States'))
        self.assertFalse(finder.place_matches('paris, md', 'Paris Road, Baltimore County, Maryland'))

    def test_the_real_place_matches(self):
        self.assertTrue(finder.place_matches('bel air, md', 'Bel Air, Harford County, Maryland, United States'))
        self.assertTrue(finder.place_matches('st. paul, mn', 'Saint Paul, Ramsey County, Minnesota'))
        self.assertTrue(finder.place_matches('new york, ny', 'City of New York, New York, United States'))

    def test_job_cities_use_the_strict_check_but_selected_cities_do_not(self):
        class Cursor:
            def __init__(self): self.row = {'latitude': 39.7, 'longitude': -75.79, 'display_name': 'New London Road, Cecil County'}
            def execute(self, *args): pass
            def fetchone(self): return self.row
            def close(self): pass

        class Database:
            def cursor(self, *args, **kw): return Cursor()

        self.assertIsNone(finder.geocode_location(Database(), 'london, md', require_place_match=True))
        self.assertIsNotNone(finder.geocode_location(Database(), 'london, md'))


class StateFromThePage(unittest.TestCase):
    def test_a_country_dropdown_does_not_set_the_state(self):
        html = ('<p>Remote role open across the U.S.</p><select><option>Turkey</option><option>Georgia</option>'
                '<option>Vietnam</option></select>')
        self.assertIsNone(usa_location.analyze_usa_location(html)['state'])

    def test_a_state_written_in_the_page_text_still_counts(self):
        self.assertEqual(usa_location.analyze_usa_location('<p>Our office: Atlanta, Georgia</p>')['state'], 'GA')

    def test_the_listings_own_location_wins_over_the_page(self):
        URL = 'https://example.com/jobs/web-designer'
        data = {'@type': 'JobPosting', 'title': 'Web Designer', 'url': URL, 'hiringOrganization': {'name': 'Example'},
                'jobLocation': {'address': {'addressLocality': 'Towson', 'addressRegion': 'Maryland', 'addressCountry': 'US'}}}
        pages = {URL: '<script type="application/ld+json">' + json.dumps(data) + '</script>'}
        records = []
        location = lambda *args, **kw: {'country': 'United States', 'state': 'GA', 'score': 8, 'evidence': []}
        run_search(pages, URL, 'Web Designer', max_new=1, location=location, records=records)
        self.assertEqual(records[0][8], 'MD')  # state


class OutsideTheUsLeads(unittest.TestCase):
    def test_unsaved_non_us_leads_are_rejected_but_saved_and_remote_ok_are_not(self):
        lead = {'is_kept': 0, 'source_type': 'SearXNG'}
        self.assertTrue(finder.is_wrong_location_lead(lead, {'location': 'London, GB'}))
        self.assertFalse(finder.is_wrong_location_lead(dict(lead, is_kept=1), {'location': 'London, GB'}))
        self.assertFalse(finder.is_wrong_location_lead(dict(lead, source_type='Remote OK'), {'location': 'London, GB'}))
        self.assertFalse(finder.is_wrong_location_lead(lead, {'location': 'Towson, Maryland, US'}))
        self.assertFalse(finder.is_wrong_location_lead(lead, {}))


class RemoteRestrictions(unittest.TestCase):
    def states(self, text, job_state=None):
        return sorted(usa_location.remote_state_restrictions(text, job_state))

    def test_named_states_are_found(self):
        self.assertEqual(self.states('Candidates must reside in Texas or Florida.'), ['FL', 'TX'])
        self.assertEqual(self.states('Residents of Colorado only'), ['CO'])
        self.assertEqual(self.states('Open only to candidates in NY, NJ or CT'), ['CT', 'NJ', 'NY'])
        self.assertEqual(self.states('Must reside in West Virginia'), ['WV'])

    def test_not_out_of_state_means_the_jobs_own_state(self):
        self.assertEqual(self.states('You cannot be out of state for this role', 'VA'), ['VA'])
        self.assertEqual(self.states('You cannot be out of state for this role'), [])

    def test_ordinary_remote_wording_is_not_a_restriction(self):
        self.assertEqual(self.states('Work from anywhere in the US'), [])
        self.assertEqual(self.states('Headquartered in Austin, Texas; fully remote across the US'), [])
        self.assertEqual(self.states('Applicants must live in the US'), [])

    def test_selected_states_come_from_every_place_the_user_picked(self):
        self.assertEqual(usa_location.selected_state_codes('MD, DE', [{'city': 'Baltimore, MD'}, {'city': 'Virginia'}], set()),
                         {'MD', 'DE', 'VA'})


class RemoteJobsInSearch(unittest.TestCase):
    URL = 'https://example.com/jobs/web-designer'

    def search(self, description, cities=None, state='MD'):
        data = {'@type': 'JobPosting', 'title': 'Web Designer', 'url': self.URL, 'jobLocationType': 'TELECOMMUTE',
                'hiringOrganization': {'name': 'Example'}, 'description': description}
        pages = {self.URL: '<script type="application/ld+json">' + json.dumps(data) + '</script>'}
        location = lambda *args, **kw: {'country': 'United States', 'state': 'TX', 'score': 8, 'evidence': []}
        skips = []
        saved = run_search(pages, self.URL, 'Web Designer', max_new=1, location=location,
                           cities_json=json.dumps(cities or [{'city': 'Maryland', 'radius': 50}]),
                           extra={'record_skip': lambda *args: skips.append(args[0])})
        return saved, skips

    def test_remote_job_anywhere_in_the_us_stays(self):
        saved, _ = self.search('Fully remote. Work from anywhere in the United States.')
        self.assertEqual(saved, {self.URL})

    def test_remote_job_limited_to_another_state_is_skipped_with_a_reason(self):
        saved, skips = self.search('This remote role requires that you must reside in Texas.')
        self.assertEqual(saved, set())
        self.assertEqual(skips, ['Remote job limited to residents of TX'])

    def test_remote_job_limited_to_a_selected_state_stays(self):
        saved, _ = self.search('Remote, but you must be located in the state of Maryland.')
        self.assertEqual(saved, {self.URL})


class FooterAddresses(unittest.TestCase):
    PAGE = ('<html><body><main><h1>Web Designer</h1><p>Design our websites.</p></main>'
            '<footer>Corcoran School of the Arts &amp; Design 500 17th Street, NW Washington, D.C. 20006 Phone: 202-994-1700</footer>'
            '</body></html>')

    def test_the_footer_address_is_used_only_on_the_employers_own_site(self):
        self.assertEqual(finder.extract_job_city(self.PAGE, 'Web Designer', allow_footer=True), ('Washington', 'DC'))
        # On a job board the footer is the board's address, not the job's.
        self.assertEqual(finder.extract_job_city(self.PAGE, 'Web Designer'), (None, None))

    def test_the_listings_own_location_still_wins_over_the_footer(self):
        page = self.PAGE.replace('<p>Design our websites.</p>', '<p>Location: Baltimore, MD 21201</p>')
        self.assertEqual(finder.extract_job_city(page, '', allow_footer=True), ('Baltimore', 'MD'))

    def test_a_subdomain_of_the_verified_company_site_counts_as_its_own_site(self):
        details = {'employer_site': {'domain': 'gwu.edu'}}
        self.assertTrue(company_names.on_company_site('https://corcoran.gwu.edu/web-designer', 'Corcoran School of the Arts & Design', details))
        self.assertFalse(company_names.on_company_site('https://graphic-design.thecreativeloft.com/job/1', 'Corcoran School of the Arts & Design', details))
        self.assertTrue(company_names.on_company_site('https://www.acmedesign.com/jobs/1', 'Acme Design', {}))


class PlaceChoice(unittest.TestCase):
    def test_a_real_town_beats_a_bigger_ranked_village_of_the_same_name(self):
        results = [
            {'display_name': 'Bel Air, Triple Lakes, Allegany County', 'lat': '39.5797', 'lon': '-78.8531', 'importance': 0.376, 'type': 'statistical'},
            {'display_name': 'Bel Air, Allegany County, 21556', 'lat': '39.5731', 'lon': '-78.8497', 'importance': 0.376, 'type': 'village'},
            {'display_name': 'Bel Air, Harford County, 21014', 'lat': '39.5355', 'lon': '-76.3490', 'importance': 0.187, 'type': 'administrative'},
        ]
        self.assertIn('Harford County', finder.pick_place(results)['display_name'])

    def test_importance_breaks_ties_between_places_of_the_same_kind(self):
        results = [{'display_name': 'A', 'importance': 0.2, 'type': 'city'}, {'display_name': 'B', 'importance': 0.7, 'type': 'city'}]
        self.assertEqual(finder.pick_place(results)['display_name'], 'B')

    def test_cached_answers_from_before_the_ranking_fix_are_not_reused(self):
        self.assertTrue(finder._normalize_geocode_query('Bel Air, MD').endswith('[v2]'))


if __name__ == '__main__':
    unittest.main()
