import json
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import no_database  # noqa: F401  (cuts tests off from the real database)
from owners import search_source
import job_finder
from jobfinder.records import job_retention

ROOT = Path(__file__).resolve().parents[1]
BEL_AIR = {"latitude": 39.5359, "longitude": -76.3483}
TOWSON_TARGET = {"city": "Towson, MD", "radius": 20, "lat": 39.4015, "lon": -76.6019}  # Bel Air is about 16 miles away


class Db:
    """Just enough of a database: rejected rows and closing-date rows to read, and a record of every statement."""

    def __init__(self, rejected=(), closing=()):
        self.rejected, self.closing = list(rejected), list(closing)
        self.statements, self.commits = [], 0
        db = self

        class Cursor:
            rowcount = 1

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def execute(self, sql, params=None):
                self.sql = " ".join(sql.split())
                db.statements.append((self.sql, params))

            def fetchall(self):
                if "WHERE is_rejected = 1" in self.sql:
                    return list(db.rejected)
                if "listing_details LIKE" in self.sql:
                    return list(db.closing)
                return []

            def close(self):
                pass

        self._cursor = Cursor()

    def cursor(self, *args, **kwargs):
        return self._cursor

    def commit(self):
        self.commits += 1

    def updates(self):
        return [(sql, params) for sql, params in self.statements if sql.startswith("UPDATE")]


def rejected_row(row_id, title, reason, **place):
    return {"id": row_id, "career_job_title": title, "state": place.pop("state", "Maryland"), "latitude": None,
            "longitude": None, "rejection_reason": reason, **place}


class Scope(unittest.TestCase):
    def setUp(self):
        folder = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(folder, ignore_errors=True))
        self.scope_file = Path(folder) / "search_scope.json"

    def recheck(self, database, titles, cities=(), state="Maryland", targets=()):
        profile = (list(titles), [dict(c) for c in cities], state, None)
        with patch.object(job_finder, "load_profile_filters", return_value=profile), \
                patch.object(job_finder, "prepare_city_targets", return_value=list(targets)):
            return job_finder.recheck_system_rejections(database, self.scope_file)

    def test_the_first_check_only_remembers_the_search_and_restores_nothing(self):
        database = Db([rejected_row(1, "Web Designer", "wrong_role")])
        self.assertEqual(self.recheck(database, ["web designer"]), 0)
        self.assertTrue(self.scope_file.exists())
        self.assertEqual(database.updates(), [])

    def test_an_unchanged_search_restores_nothing(self):
        database = Db([rejected_row(1, "Web Designer", "wrong_role")])
        self.recheck(database, ["web designer"])
        self.assertEqual(self.recheck(database, ["web designer"]), 0)
        self.assertEqual(database.updates(), [])

    def test_adding_a_job_title_restores_system_rejections_that_now_fit(self):
        database = Db([rejected_row(1, "Production Specialist", "wrong_role"), rejected_row(2, "Forklift Operator", "wrong_role")])
        self.recheck(database, ["web designer"])
        restored = self.recheck(database, ["web designer", "production specialist"])
        self.assertEqual(restored, 1)
        [(sql, params)] = database.updates()
        self.assertEqual(params, (1,))
        self.assertIn("is_rejected = 0", sql)
        self.assertIn("rejected_by = NULL", sql)
        self.assertIn("rejected_by = 'system'", sql)  # never touches a row the person rejected

    def test_only_the_systems_rejections_are_looked_at(self):
        database = Db([])
        self.recheck(database, ["web designer"])
        self.recheck(database, ["web designer", "graphic design"])
        select = next(sql for sql, _ in database.statements if "WHERE is_rejected = 1" in sql)
        self.assertIn("rejected_by = 'system'", select)
        self.assertIn("rejection_reason IN", select)

    def test_a_bigger_radius_or_new_city_restores_listings_that_are_now_close_enough(self):
        near = rejected_row(3, "Web Designer", "wrong_location", **BEL_AIR)
        far = rejected_row(4, "Web Designer", "wrong_location", latitude=47.6, longitude=-122.3, state="Washington")
        no_place = rejected_row(5, "Web Designer", "wrong_location", state="Texas")
        database = Db([near, far, no_place])
        old = [{"city": "Towson, MD", "radius": 5}]
        self.recheck(database, ["web designer"], old, targets=[dict(TOWSON_TARGET, radius=5)])
        restored = self.recheck(database, ["web designer"], [{"city": "Towson, MD", "radius": 20}], targets=[TOWSON_TARGET])
        self.assertEqual(restored, 1)
        self.assertEqual(database.updates()[0][1], (3,))

    def test_a_statewide_search_restores_listings_in_that_state(self):
        in_state = rejected_row(6, "Web Designer", "wrong_location", state="Delaware")
        database = Db([in_state])
        self.recheck(database, ["web designer"], [{"city": "Towson, MD", "radius": 20}], targets=[TOWSON_TARGET])
        restored = self.recheck(database, ["web designer"], [{"city": "Towson, MD", "radius": 20}, {"city": "Delaware", "radius": 0}],
                                targets=[TOWSON_TARGET])
        self.assertEqual(restored, 1)

    def test_the_quiet_version_never_stops_a_run(self):
        job_finder.recheck_system_rejections_quietly(object())


class WhoRejected(unittest.TestCase):
    def test_the_systems_own_rejections_are_marked_system(self):
        database = Db()
        job_finder.reject_irrelevant_row(database, 9, "wrong_role")
        [(sql, params)] = database.updates()
        self.assertIn("rejected_by = 'system'", sql)
        self.assertEqual(params, ("wrong_role", 9))

    def test_the_reject_button_marks_user_and_restore_clears_it(self):
        source = (ROOT / "dashboard.py").read_text(encoding="utf-8")
        self.assertIn("rejection_reason = %s, rejected_by = 'user'", source)
        self.assertIn("rejected_at = NULL, rejection_reason = NULL, rejected_by = NULL", source)
        self.assertIn("ADD COLUMN IF NOT EXISTS rejected_by VARCHAR(10) NULL", source)
        self.assertIn('"rejected_by": "VARCHAR(10) NULL AFTER rejected_at"', search_source())


class ClosedJobs(unittest.TestCase):
    def test_a_listing_past_its_closing_date_is_marked_closed(self):
        past = json.dumps({"closes": "2026-10-01"})
        today = json.dumps({"closes": "2026-10-02"})
        future = json.dumps({"closes": "2026-10-08"})
        database = Db(closing=[(1, past), (2, today), (3, future), (4, json.dumps({"closes": "soon"})), (5, "not json")])
        self.assertEqual(job_finder.close_expired_listings(database, today=date(2026, 10, 2)), 1)
        [(sql, params)] = database.updates()
        self.assertIn("job_open_status = 'Closed'", sql)
        self.assertEqual(params[-1], 1)
        self.assertEqual(database.commits, 1)

    def test_nothing_to_close_changes_nothing(self):
        database = Db(closing=[(1, json.dumps({"closes": "2026-12-31"}))])
        self.assertEqual(job_finder.close_expired_listings(database, today=date(2026, 10, 2)), 0)
        self.assertEqual(database.updates(), [])

    def test_closed_unsaved_jobs_are_deleted_after_a_week_and_saved_ones_never(self):
        self.assertEqual(job_retention.CLOSED_KEEP_DAYS, 7)
        self.assertIn("Applied", job_retention.SAVED_STATUSES)

    def test_closed_unsaved_jobs_are_hidden_from_the_results(self):
        source = (ROOT / "dashboard.py").read_text(encoding="utf-8")
        self.assertIn('row.get("job_open_status") == "Closed" and not row.get("is_kept")', source)
        self.assertIn('row.get("application_status") not in SAVED_STATUSES', source)

    def test_every_search_and_refresh_starts_by_re_checking_and_closing(self):
        source = search_source()
        self.assertEqual(source.count("    recheck_system_rejections_quietly(database)\n"), 2)
        self.assertEqual(source.count("    close_expired_listings_quietly(database)\n"), 2)


if __name__ == "__main__":
    unittest.main()
