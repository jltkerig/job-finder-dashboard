import json
import unittest
from types import SimpleNamespace
from unittest import mock

from jobfinder.web import link_jobs

PAGE = ('<html><head><meta property="og:title" content="The Hartford hiring Web Designer in Wayne, PA | LinkedIn"></head>'
        '<body><p>' + "Design pages in HTML and CSS. " * 40 + "</p></body></html>")


class FakeCursor:
    def __init__(self, store):
        self.store, self.last = store, None

    def execute(self, sql, params=None):
        self.store.setdefault("sql", []).append((" ".join(sql.split()), params))
        self.last = sql

    def fetchone(self):
        if "WHERE source_url" in self.last:
            return self.store.get("row")
        if "SELECT listing_details" in self.last:
            return {"listing_details": json.dumps(self.store.get("details", {}))}


class FakeConnection:
    def __init__(self, store):
        self.store = store

    def cursor(self, **kwargs):
        return FakeCursor(self.store)

    def commit(self):
        pass

    def close(self):
        pass


class LinkJobTests(unittest.TestCase):
    def test_links_get_https_and_junk_is_refused(self):
        self.assertEqual(link_jobs.clean_url("acme.com/j/1"), "https://acme.com/j/1")
        self.assertEqual(link_jobs.clean_url("not a link"), "")
        self.assertEqual(link_jobs.clean_url("javascript:alert(1)"), "")

    def test_a_linkedin_title_gives_the_job_and_the_company(self):
        self.assertEqual(link_jobs.title_and_company(PAGE, "www.linkedin.com"), ("Web Designer", "The Hartford"))

    def test_a_new_link_is_saved_as_a_job_and_queued(self):
        store = {}
        saved = {}

        def fake_save(connection, name, title, credibility, domain, career_url, source_url, *args, **kwargs):
            saved.update(name=name, title=title, url=source_url, details=kwargs["listing_details"], type=kwargs["source_type"])
            store["row"] = {"id": 77, "name": name, "career_job_title": title}
            return True

        with mock.patch.object(link_jobs, "fetch_text", return_value=SimpleNamespace(url="x", text=PAGE)), \
             mock.patch.object(link_jobs, "posting_text", return_value=""), \
             mock.patch.object(link_jobs.db, "connect", return_value=FakeConnection(store)), \
             mock.patch.object(link_jobs, "save_company", fake_save), \
             mock.patch.object(link_jobs, "ensure_keep_column"), mock.patch.object(link_jobs, "ensure_job_tracking_columns"):
            result = link_jobs.add_by_link("https://www.linkedin.com/jobs/view/1")
        self.assertEqual((result["id"], result["title"], result["company"], result["already"]), (77, "Web Designer", "The Hartford", False))
        self.assertTrue(saved["details"]["added_by_link"])
        self.assertEqual(saved["type"], "Added by link")
        queued = [p for sql, p in store["sql"] if sql.startswith("UPDATE companies SET listing_details")]
        self.assertTrue(json.loads(queued[0][0])["apply_queued"])

    def test_a_link_already_saved_is_only_queued(self):
        store = {"row": {"id": 5, "name": "Acme", "career_job_title": "Designer"}}
        with mock.patch.object(link_jobs, "fetch_text", return_value=None), mock.patch.object(link_jobs, "posting_text", return_value=""), \
             mock.patch.object(link_jobs.db, "connect", return_value=FakeConnection(store)), \
             mock.patch.object(link_jobs, "save_company") as save, \
             mock.patch.object(link_jobs, "ensure_keep_column"), mock.patch.object(link_jobs, "ensure_job_tracking_columns"):
            result = link_jobs.add_by_link("https://acme.com/j/1")
        self.assertTrue(result["already"])
        save.assert_not_called()

    def test_the_route_refuses_a_bad_link(self):
        import dashboard
        client = dashboard.app.test_client()
        with client.session_transaction() as session:
            session["csrf_token"] = "t"
        reply = client.post("/apply-queue/add-link", json={"url": "nonsense"}, headers={"X-CSRF-Token": "t"})
        self.assertEqual(reply.status_code, 400)


if __name__ == "__main__":
    unittest.main()


class CompanyNameGuards(unittest.TestCase):
    def test_a_place_in_the_page_title_is_not_taken_for_the_company(self):
        html = '<html><head><title>Web Developer II - Houston, Texas, United States | University of Houston</title></head></html>'
        self.assertEqual(link_jobs.title_and_company(html, "careers.uh.edu"), ("Web Developer II", "University of Houston"))
        html = '<html><head><meta property="og:title" content="Web Developer II | Houston, Texas, United States"></head></html>'
        title, company = link_jobs.title_and_company(html, "careers.uh.edu")
        self.assertNotIn("Houston", company)

    def test_linkedins_own_notices_are_never_a_company_name(self):
        from jobfinder.search.company_names import tidy_company_name
        for notice in ("application was viewed", "Your application was viewed", "Your resume was downloaded", "Application was sent"):
            self.assertEqual(tidy_company_name(notice), "")
        self.assertEqual(tidy_company_name("Acme Health"), "Acme Health")


class ICIMSStatus(unittest.TestCase):
    def test_an_icims_job_is_read_from_its_framed_page_so_an_open_job_is_not_called_closed(self):
        from jobfinder.sources.employer_enterprise import ICIMS
        asked = []

        class Http:
            def get(self, url, accept=None):
                asked.append(url)
                framed = "in_iframe=1" in url
                return SimpleNamespace(status_code=200, text='{"@type": "JobPosting"}' if framed else "<html>Privacy Policy</html>")

        board = ICIMS({"host": "careers.example.com"})
        self.assertEqual(board.status("https://careers.example.com/jobs/12/web-developer/job", Http()), "Open")
        self.assertIn("in_iframe=1", asked[0])

    def test_a_missing_icims_job_is_closed(self):
        from jobfinder.sources.employer_enterprise import ICIMS

        class Http:
            def get(self, url, accept=None):
                return SimpleNamespace(status_code=404, text="")

        self.assertEqual(ICIMS({"host": "careers.example.com"}).status("https://careers.example.com/jobs/9/x/job", Http()), "Closed")
