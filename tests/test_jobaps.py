"""JobAps portals (the State of Maryland's job openings): the RSS feed, the job pages and the board recognizer.
Every job, agency and address below is made up; only the shape of the portal's feed and pages is real."""
import html
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import no_database  # noqa: F401  (cuts tests off from the real database)
from jobfinder.sources import employer_jobaps, employer_jobs
from jobfinder.sources.ats_discovery import identify, pretty_name
from jobfinder.sources.employer_jobs import DEFAULT_EMPLOYERS, Employer, employer_for_url, load_employers

CONFIG = {"name": "State of Example", "system": "jobaps", "agency": "MD", "state": "MD", "domain": "example.example.gov"}
LINK = "https://www.jobapscloud.com/MD/sup/BulPreview.asp?R1=26&R2={number}&R3=0001"


def item(title, number, places=("Harford",), kind="Full-Time", department="EXAMPLE - Office of Digital Services"):
    """One <item> as the portal writes it: the description is HTML, escaped inside the XML."""
    places_html = "".join(f"<li>{place}</li>" for place in places)
    description = (f"<p><strong>Department:</strong> {department}</p><p><strong>Date Opened:</strong> 10/14/2026 4:00:00 PM.</p>"
                   f"<p><strong>Filing Deadline:</strong> 10/28/2026 5:00:00 PM</p>"
                   f"<p><strong>Salary:</strong> $60,000.00 - $90,000.00/year.</p>"
                   f"<p><strong>Employment Type:</strong> <ul><li>{kind}</li></ul></p>"
                   f"<p><strong>Work Location:</strong> <ul>{places_html}</ul></p>")
    link = LINK.format(number=number).replace("&", "&amp;")
    return (f"<item>\n <title>{html.escape(title)} - #26-{number}-0001\n </title>\n <link>{link}</link>\n"
            f" <guid isPermaLink=\"true\">{link}</guid>\n <description>\n{html.escape(description)}\n </description>\n</item>\n")


FEED = ('<?xml version="1.0" encoding="ISO-8859-1" ?>\n<rss version="2.0"><channel><title>State of Example</title>\n'
        + item("ADMINISTRATOR II (Web Developer)", "000101", ("Harford", "Baltimore City"))
        + item("ACCOUNTANT II (Accountant II, General Accounting)", "000102", ("Howard",))
        + item("Nurse Practitioner (NURSE PRACTITIONER)", "000103", ("Washington County",), kind="Part-Time")
        + "</channel></rss>")

PAGE = ("<html><head><title>Job Announcement: ADMINISTRATOR II</title>"
        '<script type="application/ld+json">' + json.dumps({
            "@context": "https://schema.org", "@type": "JobPosting", "title": "ADMINISTRATOR II",
            "description": "<p>Introduction</p><p>Build the website.\\r\\nUse HTML and CSS.</p>", "datePosted": "2026-10-09",
            "employmentType": "Full-Time", "jobLocation": {"address": {"addressLocality": "Harford", "addressRegion": "MD"}}}) + "</script>"
        '</head><body><h1 id="JobBulletinTitle">ADMINISTRATOR II</h1><h2>GRADE</h2><p>STD0020</p>'
        "<h2>LOCATION OF POSITION</h2><p>Example Office 100 Main Street Bel Air, MD 21014</p><h2>POSITION DUTIES</h2><p>Build things.</p>"
        "</body></html>")


class Response:
    def __init__(self, text="", status=200):
        self.text, self.status_code, self.content = text, status, text.encode("utf-8")


class FakeHttp:
    def __init__(self, feed=FEED, page=PAGE, feed_status=200, page_status=200):
        self.feed, self.page, self.feed_status, self.page_status, self.calls = feed, page, feed_status, page_status, []

    def get(self, url, params=None, accept=None, headers=None):
        self.calls.append(url)
        if url.endswith("/rss.asp"):
            return Response(self.feed, self.feed_status)
        return Response(self.page, self.page_status)


def known_towns(*names):
    """A stand-in for the Census places table that knows only these towns (lower case, "town, st")."""
    return lambda text, state="": (39.5, -76.3) if str(text).casefold() in names else None


class Titles(unittest.TestCase):
    def test_the_working_title_is_what_the_job_is(self):
        for raw, shown in [
            ("ACCOUNTANT II (Accountant II, General Accounting) - #26-004547-0006", "Accountant II, General Accounting"),
            ("Barber Stylist Instructor (TEACHER CONDITIONAL) - #26-005885-0004", "Barber Stylist Instructor"),
            ("Procurement Officer II - #26-003003-0016", "Procurement Officer II"),
            ("*REPOST* SYSTEMS CONTROL ACCOUNTANT SUPERVISOR (General Accounting Division) - #26-004582-0001", "Systems Control Accountant Supervisor"),
            ("IT PROGRAMMER ANALYST, LEAD/ADVANCED (FULL-TIME CONTRACTUAL) - #26-004471-0005", "IT Programmer Analyst, Lead/Advanced (Full-Time Contractual)"),
            ("Minority Business Enterprise Program (MBE) Compliance Officer (ADMINISTRATIVE OFFICER III) - #26-002247-0066",
             "Minority Business Enterprise Program (MBE) Compliance Officer"),
        ]:
            self.assertEqual(employer_jobaps.working_title(raw), shown, raw)

    def test_a_job_number_comes_from_the_address_or_the_title(self):
        self.assertEqual(employer_jobaps.job_number("https://www.jobapscloud.com/MD/sup/bulpreview.asp?b=&R1=26&R2=004582&R3=0001"), "26-004582-0001")
        self.assertEqual(employer_jobaps.job_number("https://www.jobapscloud.com/MD/sup/BulPreview.asp?R1=26&R2=004547&R3=0006"), "26-004547-0006")
        self.assertEqual(employer_jobaps.job_number("Procurement Officer II - #26-003003-0016"), "26-003003-0016")
        self.assertEqual(employer_jobaps.job_number("https://www.jobapscloud.com/MD/"), "")


class Feed(unittest.TestCase):
    def test_every_opening_is_read_from_the_feed(self):
        jobs = employer_jobaps.parse_feed(FEED)
        self.assertEqual([job["id"] for job in jobs], ["26-000101-0001", "26-000102-0001", "26-000103-0001"])
        first = jobs[0]
        self.assertEqual(first["title"], "Web Developer")
        self.assertEqual(first["places"], ["Harford", "Baltimore City"])
        self.assertEqual(first["salary"], "$60,000.00 - $90,000.00/year")
        self.assertEqual((first["kind"], first["department"]), ("Full-Time", "EXAMPLE - Office of Digital Services"))
        self.assertEqual(first["deadline"], "10/28/2026 5:00:00 PM")
        self.assertEqual(jobs[2]["kind"], "Part-Time")
        self.assertEqual(jobs[2]["places"], ["Washington County"])

    def test_junk_is_skipped(self):
        self.assertEqual(employer_jobaps.parse_feed("<rss></rss>"), [])
        self.assertEqual(employer_jobaps.parse_feed("<item><title>No link</title></item>"), [])


class Searching(unittest.TestCase):
    def setUp(self):
        self.employer = Employer(CONFIG)
        patcher = patch.object(employer_jobaps, "place_point", known_towns("bel air, md", "baltimore, md"))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_a_matching_job_is_found_with_one_feed_request_and_one_page(self):
        http = FakeHttp()
        (opening,) = self.employer.find_openings(["Web Developer"], http)
        self.assertEqual(len(http.calls), 2)  # the feed, then the one job's page
        self.assertEqual(opening["title"], "Web Developer")
        self.assertEqual(opening["url"], "https://www.jobapscloud.com/MD/sup/BulPreview.asp?R1=26&R2=000101&R3=0001")
        self.assertEqual(opening["salary"], "$60,000.00 - $90,000.00/year")
        self.assertEqual(opening["schedule"], "FULL_TIME")
        self.assertEqual(opening["posted"], "2026-10-09")
        self.assertIn("Use HTML and CSS", opening["description"])
        self.assertNotIn("\\r\\n", opening["description"])
        self.assertIn("Apply by: 10/28/2026 5:00:00 PM", opening["description"])
        self.assertIn("careers site", opening["evidence"][0])

    def test_the_town_is_the_address_town_then_the_county_seats(self):
        (opening,) = self.employer.find_openings(["Web Developer"], FakeHttp())
        self.assertEqual(opening["locations"], ["Bel Air, MD, US", "Baltimore, MD, US"])  # Harford's seat is also Bel Air: once
        self.assertEqual(opening["location"], "Bel Air, MD, US")

    def test_an_address_town_that_is_not_known_falls_back_to_the_county(self):
        with patch.object(employer_jobaps, "place_point", known_towns()):
            (opening,) = self.employer.find_openings(["Web Developer"], FakeHttp())
        self.assertEqual(opening["locations"], ["Bel Air, MD, US", "Baltimore, MD, US"])

    def test_only_matching_jobs_cost_a_page_request(self):
        http = FakeHttp()
        self.assertEqual(self.employer.find_openings(["Florist"], http), [])
        self.assertEqual(len(http.calls), 1)

    def test_a_feed_that_is_down_is_an_error_for_the_search_to_report(self):
        with self.assertRaises(ValueError):
            self.employer.find_openings(["Web Developer"], FakeHttp(feed_status=503))

    def test_a_job_page_that_is_gone_is_left_out(self):
        self.assertEqual(self.employer.find_openings(["Web Developer"], FakeHttp(page_status=404)), [])

    def test_a_page_without_posting_data_still_gives_the_listing(self):
        (opening,) = self.employer.find_openings(["Web Developer"], FakeHttp(page="<html><body><h1>Job</h1></body></html>"))
        self.assertEqual(opening["title"], "Web Developer")
        self.assertIn("EXAMPLE - Office of Digital Services", opening["description"])
        self.assertEqual(opening["posted"], "2026-10-14")  # from the feed's "Date Opened"

    def test_an_opening_is_open_while_it_is_in_the_feed(self):
        employer = Employer(CONFIG)
        http = FakeHttp()
        url = "https://www.jobapscloud.com/MD/sup/bulpreview.asp?b=&R1=26&R2=000101&R3=0001"
        self.assertEqual(employer.status(url, http), "Open")
        self.assertEqual(employer.status("https://www.jobapscloud.com/MD/sup/BulPreview.asp?R1=26&R2=999999&R3=0001", http), "Closed")
        self.assertEqual(employer.status("https://www.jobapscloud.com/MD/", http), "Unknown")
        self.assertEqual(len([call for call in http.calls if call.endswith("/rss.asp")]), 1)  # the feed is read once per run

    def test_an_empty_feed_does_not_close_everything(self):
        url = "https://www.jobapscloud.com/MD/sup/BulPreview.asp?R1=26&R2=000101&R3=0001"
        self.assertEqual(Employer(CONFIG).status(url, FakeHttp(feed="<rss></rss>")), "Unknown")


class Recognizing(unittest.TestCase):
    def test_the_portals_addresses_are_recognized(self):
        for url in ("https://www.jobapscloud.com/MD/sup/BulPreview.asp?R1=26&R2=004547&R3=0006",
                    "https://www.jobaps.com/md/jobs/mdod", "https://jobapscloud.com/MD/"):
            self.assertEqual(identify(url), {"system": "jobaps", "agency": "MD", "state": "MD"}, url)
        self.assertEqual(identify("https://www.jobapscloud.com/lacounty/default.asp"), {"system": "jobaps", "agency": "LACOUNTY"})
        self.assertIsNone(identify("https://www.jobapscloud.com/css/site.css"))
        self.assertIsNone(identify("https://example.example.com/MD/"))

    def test_the_state_has_its_own_name(self):
        self.assertEqual(pretty_name({"system": "jobaps", "agency": "MD"}), "State of Maryland")

    def test_the_state_of_maryland_is_one_of_the_watched_employers(self):
        self.assertTrue(any(config.get("system") == "jobaps" and config["agency"] == "MD" for config in DEFAULT_EMPLOYERS))
        mine = [employer for employer in load_employers() if employer.system == "jobaps"]
        self.assertTrue(mine and isinstance(mine[0].adapter, employer_jobaps.JobAps))

    def test_a_job_address_finds_its_employer(self):
        employers = [Employer(CONFIG), Employer({"name": "Other", "system": "neogov", "agency": "other"})]
        found = employer_for_url("https://www.jobapscloud.com/md/sup/BulPreview.asp?R1=26&R2=000101&R3=0001", employers)
        self.assertEqual(found.name, "State of Example")
        self.assertIsNone(employer_for_url("https://www.jobapscloud.com/LACOUNTY/sup/BulPreview.asp?R1=26&R2=1&R3=1", employers))


if __name__ == "__main__":
    unittest.main()
