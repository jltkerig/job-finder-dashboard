from jobfinder.web import schema
import sys
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import no_database  # noqa: F401  (cuts tests off from the real database)
import dashboard

ROOT = Path(__file__).resolve().parents[1]


def row(titles, state="Maryland", cities="[]", minute=0):
    return {"job_title": titles, "state": state, "cities_json": cities, "searched_at": datetime(2026, 10, 2, 12, minute)}


class FakeConnection:
    """A database that remembers every statement and answers the "same search?" lookup."""

    def __init__(self, earlier_id=None):
        self.statements, self.committed = [], False
        self.earlier_id = earlier_id
        connection = self

        class Cursor:
            def execute(self, sql, params=None):
                connection.statements.append((" ".join(sql.split()), params))

            def fetchone(self):
                return (connection.earlier_id,) if connection.earlier_id else None

            def close(self):
                pass

        self._cursor = Cursor()

    def cursor(self, *args, **kwargs):
        return self._cursor

    def commit(self):
        self.committed = True

    def is_connected(self):
        return True

    def close(self):
        pass


class RecentSearchDisplay(unittest.TestCase):
    def test_the_main_title_is_the_first_and_the_others_follow(self):
        self.assertEqual(dashboard.split_search_titles("frontend web developer, graphic design ,web designer"),
                         ("frontend web developer", ["graphic design", "web designer"]))
        self.assertEqual(dashboard.split_search_titles("Web Designer"), ("Web Designer", []))
        self.assertEqual(dashboard.split_search_titles(""), ("", []))

    def test_repeated_searches_show_once_newest_first(self):
        rows = [row("web designer, graphic design", minute=30), row("Web Designer", minute=20),
                row("web designer, graphic design", minute=10), row("web designer, graphic design", state="Delaware", minute=5),
                row("WEB DESIGNER, GRAPHIC DESIGN", minute=1)]
        shown = dashboard.collapse_search_history(rows, limit=10)
        self.assertEqual([(r["job_title"], r["state"], r["searched_at"].minute) for r in shown],
                         [("web designer, graphic design", "Maryland", 30), ("Web Designer", "Maryland", 20),
                          ("web designer, graphic design", "Delaware", 5)])
        self.assertEqual(shown[0]["main_title"], "web designer")
        self.assertEqual(shown[0]["other_titles"], ["graphic design"])

    def test_the_same_title_in_different_cities_is_a_different_search(self):
        a = row("web designer", cities='[{"city": "Bel Air, MD", "radius": 20}]')
        b = row("web designer", cities='[{"city": "Towson, MD", "radius": 20}]')
        self.assertEqual(len(dashboard.collapse_search_history([a, b])), 2)

    def test_only_the_limit_is_shown(self):
        rows = [row(f"title {n}") for n in range(30)]
        self.assertEqual(len(dashboard.collapse_search_history(rows, limit=10)), 10)

    def test_the_page_shows_the_main_title_large_and_the_others_small(self):
        html = (ROOT / "templates" / "user-dashboard.html").read_text(encoding="utf-8")
        self.assertIn('class="history-title"', html)
        self.assertIn('class="history-titles"', html)
        self.assertIn('data-job-title="{{ search.main_title }}"', html)  # Save Job Title saves just the main one
        self.assertIn('class="bordered-button search-again-button" type="button" data-job-title="{{ search.job_title }}"', html)
        css = (ROOT / "static" / "css" / "style.css").read_text(encoding="utf-8")
        self.assertIn(".history-main .history-title { font-size:1.15rem;", css)
        self.assertIn(".history-main .history-titles { color:#5f6873; font-size:.8rem; }", css)


class RecordingASearch(unittest.TestCase):
    def record(self, earlier_id):
        connection = FakeConnection(earlier_id)
        with patch.object(schema, "ensure_job_tracking_columns"), \
                patch.object(dashboard.mysql.connector, "connect", return_value=connection):
            dashboard.record_search_history("Web Designer, Graphic Design", "Maryland", [{"city": "Bel Air, MD", "radius": 20}])
        return connection

    def test_a_new_search_is_recorded(self):
        connection = self.record(None)
        self.assertEqual(sum(1 for sql, _ in connection.statements if sql.startswith("INSERT INTO search_history")), 1)
        self.assertFalse(any(sql.startswith("UPDATE search_history") for sql, _ in connection.statements))
        self.assertTrue(connection.committed)

    def test_the_same_search_again_moves_the_earlier_entry_to_the_top_instead_of_adding_one(self):
        connection = self.record(41)
        self.assertFalse(any(sql.startswith("INSERT") for sql, _ in connection.statements))
        updates = [(sql, params) for sql, params in connection.statements if sql.startswith("UPDATE search_history")]
        self.assertEqual(len(updates), 1)
        self.assertIn("searched_at = CURRENT_TIMESTAMP", updates[0][0])
        self.assertEqual(updates[0][1], (41,))
        lookup = next(params for sql, params in connection.statements if sql.startswith("SELECT id FROM search_history"))
        self.assertEqual(lookup[0], "web designer, graphic design")  # compared without regard to case
        self.assertTrue(connection.committed)


if __name__ == "__main__":
    unittest.main()
