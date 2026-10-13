import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import no_database  # noqa: F401  (cuts tests off from the real database)
import dashboard
from jobfinder.web import listing_actions, listing_queries

ROOT = Path(__file__).resolve().parents[1]


def fake_connection(rowcount=0):
    cursor = MagicMock()
    cursor.rowcount = rowcount
    cursor.fetchall.return_value = []
    connection = MagicMock()
    connection.cursor.return_value = cursor
    return connection, cursor


class DashboardFilters(unittest.TestCase):
    def sql_for(self, status):
        connection, cursor = fake_connection()
        with patch.object(listing_queries.db, "connect", return_value=connection), \
                patch.object(listing_queries.schema, "ensure_job_tracking_columns"):
            listing_queries.get_kept_companies(status_filter=status)
        return cursor.execute.call_args[0]

    def test_applied_and_not_applied_filters(self):
        sql, params = self.sql_for("__applied")
        self.assertIn("IN ('Applied', 'Talking With Recruiter', 'Interview')", sql)
        self.assertNotIn("NOT IN", sql)
        self.assertEqual(params, [])
        sql, params = self.sql_for("__not_applied")
        self.assertIn("NOT IN ('Applied', 'Talking With Recruiter', 'Interview')", sql)

    def test_a_single_status_still_filters_exactly(self):
        sql, params = self.sql_for("Interview")
        self.assertIn("application_status = %s", sql)
        self.assertEqual(params, ["Interview"])


class BulkDelete(unittest.TestCase):
    def post(self, body, rowcount=2):
        connection, cursor = fake_connection(rowcount)
        client = dashboard.app.test_client()
        with client.session_transaction() as session:
            session["csrf_token"] = "token"
        with patch.object(listing_actions.db, "connect", return_value=connection), \
                patch.object(listing_actions, "ensure_keep_column"):
            response = client.post("/delete-kept-many", json=body, headers={"X-CSRF-Token": "token"})
        return response, cursor

    def test_only_saved_jobs_in_the_list_are_deleted(self):
        response, cursor = self.post({"ids": [3, "7"]})
        self.assertEqual(response.get_json(), {"status": "deleted", "count": 2})
        sql, params = cursor.execute.call_args[0]
        self.assertIn("is_kept = 1", sql)
        self.assertEqual(params, [3, 7])

    def test_an_empty_or_bad_list_deletes_nothing(self):
        for body in ({"ids": []}, {"ids": ["x"]}, {}):
            response, cursor = self.post(body)
            self.assertEqual(response.status_code, 400)
            cursor.execute.assert_not_called()


class DashboardPage(unittest.TestCase):
    def test_the_page_has_the_filter_checkboxes_and_the_confirmation(self):
        html = (ROOT / "templates" / "user-dashboard.html").read_text(encoding="utf-8")
        self.assertIn('value="__not_applied"', html)
        self.assertIn('class="bulk-check"', html)
        self.assertIn('id="bulk-delete"', html)
        script = (ROOT / "static" / "js" / "bulk-delete.js").read_text(encoding="utf-8")
        self.assertIn("window.confirm(", script)
        self.assertLess(script.index("window.confirm("), script.index('fetch("/delete-kept-many"'))

    def test_choosing_applied_fills_todays_date_but_keeps_it_editable(self):
        html = (ROOT / "templates" / "user-dashboard.html").read_text(encoding="utf-8")
        self.assertIn("/static/js/applied-date.js", html)
        self.assertIn('name="applied_on" type="date"', html)
        script = (ROOT / "static" / "js" / "applied-date.js").read_text(encoding="utf-8")
        self.assertIn('select.value !== "Applied"', script)
        self.assertIn("!date.value", script)  # a date you already chose is never overwritten


class SearchDuplicates(unittest.TestCase):
    def test_a_job_you_saved_stays_and_its_copy_is_the_one_hidden(self):
        copy = {"id": 1, "name": "Acme Corp", "career_job_title": "Web Designer", "source_type": "Employer careers",
                "career_credibility": 10, "date_found": "2026-10-12", "is_kept": 0, "application_status": "None"}
        mine = {"id": 2, "name": "Acme Corp", "career_job_title": "Web Designer", "source_type": "LinkedIn",
                "career_credibility": 3, "date_found": "2026-10-01", "is_kept": 1, "application_status": "Applied"}
        shown = listing_queries.merge_duplicates([copy, mine])
        self.assertEqual([row["id"] for row in shown], [2])
        self.assertEqual(shown[0]["also_listed"][0]["id"], 1)


class SavedJobCard(unittest.TestCase):
    def test_saved_jobs_have_an_apply_button_and_never_two_links_to_the_same_page(self):
        html = (ROOT / "templates" / "user-dashboard.html").read_text(encoding="utf-8")
        self.assertIn('class="bordered-button primary-action saved-apply-button"', html)
        self.assertIn("/static/js/saved-apply.js", html)
        self.assertIn("same_link", html)
        self.assertNotIn(">Career Page<", html)
        script = (ROOT / "static" / "js" / "saved-apply.js").read_text(encoding="utf-8")
        self.assertIn('"/apply-queue"', script)
        self.assertIn("queued: true", script)

    def test_a_queued_job_is_flagged_so_its_card_shows_it(self):
        from jobfinder.web import application_files
        jobs = [{"id": 1, "listing_details": '{"apply_queued": true}'}, {"id": 2, "listing_details": None}]
        with patch.object(application_files, "files_by_job", return_value={}):
            application_files.add_application_files(jobs)
        self.assertEqual([job["apply_queued"] for job in jobs], [True, False])


class TopPicksBadPick(unittest.TestCase):
    def test_a_bad_pick_leaves_the_top_10_and_is_not_picked_again(self):
        picks = (ROOT / "static" / "js" / "top-picks.js").read_text(encoding="utf-8")
        score = (ROOT / "static" / "js" / "top-picks-score.js").read_text(encoding="utf-8")
        self.assertIn('if (next === "down") dropPick(pick,', picks)
        self.assertIn('row.dataset.rating === "down"', score)


class SavedJobCardMarkup(unittest.TestCase):
    def test_the_saved_job_card_has_matching_tags(self):
        """A stray </div> once closed the collapsed row early and scrambled the open card."""
        import re
        html = (ROOT / "templates" / "user-dashboard.html").read_text(encoding="utf-8")
        start = html.index('<article class="saved-job-card"')
        card = html[start:html.index("</article>", start)]
        for tag in ("div", "details", "summary", "section", "form", "dl", "ul", "p", "span"):
            opened = len(re.findall("<" + tag + "[ >]", card))
            closed = len(re.findall("</" + tag + ">", card))
            self.assertEqual(opened, closed, f"<{tag}> opened {opened} times but closed {closed}")


if __name__ == "__main__":
    unittest.main()
