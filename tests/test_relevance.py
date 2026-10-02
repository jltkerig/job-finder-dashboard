import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import no_database  # noqa: F401  (cuts tests off from the real database)
from jobfinder.search import relevance
from jobfinder.db_schema import ensure_unique_source_index

TITLES = ['web designer', 'Website Designer', 'graphic design']
PIO = 'Public Information Officer II - GovernmentJobs.com'


def row(title, **kw):
    return dict({'career_job_title': title, 'is_kept': 0, 'source_type': 'SearXNG'}, **kw)


class Relevance(unittest.TestCase):
    def test_a_title_that_matches_none_of_the_users_titles_is_irrelevant(self):
        self.assertTrue(relevance.is_irrelevant_lead(row(PIO), {}, TITLES))

    def test_matching_titles_are_kept(self):
        self.assertFalse(relevance.is_irrelevant_lead(row('Website Designer II'), {}, TITLES))
        self.assertFalse(relevance.is_irrelevant_lead(row('Senior Web Designer - Acme'), {}, TITLES))
        self.assertFalse(relevance.is_irrelevant_lead(row('Graphic Design Intern'), {}, TITLES))

    def test_saved_vetted_remote_ok_and_untitled_rows_are_never_touched(self):
        self.assertFalse(relevance.is_irrelevant_lead(row(PIO, is_kept=1), {}, TITLES))
        self.assertFalse(relevance.is_irrelevant_lead(row(PIO), {'matched_title': 'web designer'}, TITLES))
        self.assertFalse(relevance.is_irrelevant_lead(row(PIO, source_type='Remote OK'), {}, TITLES))
        self.assertFalse(relevance.is_irrelevant_lead(row(''), {}, TITLES))

    def test_no_known_titles_changes_nothing(self):
        self.assertFalse(relevance.is_irrelevant_lead(row(PIO), {}, []))

    def test_related_onet_titles_are_added_after_the_typed_ones(self):
        expanded = relevance.expand_job_titles(['Web Designer'])
        self.assertEqual(expanded[0], 'Web Designer')
        self.assertGreater(len(expanded), 1)
        self.assertTrue(all('designer' in title.casefold() for title in expanded[1:]))
        self.assertLessEqual(len(expanded), 3)


class FakeCursor:
    def __init__(self, fetchall=None, fetchone=None, rowcount=1):
        self.statements, self._all, self._one, self.rowcount = [], list(fetchall or []), list(fetchone or []), rowcount

    def execute(self, sql, params=None):
        self.statements.append((' '.join(sql.split()), params))

    def fetchall(self):
        return self._all.pop(0)

    def fetchone(self):
        return self._one.pop(0)

    def close(self):
        pass


class FakeDatabase:
    def __init__(self, cursor):
        self._cursor, self.commits = cursor, 0

    def cursor(self, *args, **kw):
        return self._cursor

    def commit(self):
        self.commits += 1


class Database(unittest.TestCase):
    def test_rejecting_keeps_the_previous_state_for_restore(self):
        cursor = FakeCursor()
        database = FakeDatabase(cursor)
        self.assertTrue(relevance.reject_irrelevant_row(database, 13))
        sql, params = cursor.statements[0]
        self.assertIn('rejection_reason = %s', sql)
        self.assertIn('pre_reject_kept = is_kept', sql)
        self.assertIn('AND is_kept = 0', sql)
        self.assertEqual((params, database.commits), (('wrong_role', 13), 1))

    def test_a_location_rejection_uses_its_own_reason(self):
        cursor = FakeCursor()
        relevance.reject_irrelevant_row(FakeDatabase(cursor), 19, 'wrong_location')
        self.assertEqual(cursor.statements[0][1], ('wrong_location', 19))

    def test_user_titles_come_from_the_profile_and_the_latest_search(self):
        cursor = FakeCursor(fetchall=[[('web designer',), ('Visual Designer',)]],
                            fetchone=[('Web Designer, Graphic Design',)])
        self.assertEqual(relevance.load_user_titles(FakeDatabase(cursor)),
                         ['web designer', 'Visual Designer', 'Graphic Design'])


class IndexMigration(unittest.TestCase):
    def cursor_for(self, names, duplicates=0):
        cursor = FakeCursor(fetchall=[[('companies', 0, name, 1, 'x') for name in names]], fetchone=[(duplicates,)])
        return cursor

    def statements(self, cursor):
        return [sql for sql, _ in cursor.statements]

    def test_domain_unique_becomes_source_url_unique(self):
        cursor = self.cursor_for(['PRIMARY', 'domain_unique'])
        status = ensure_unique_source_index(cursor)
        statements = self.statements(cursor)
        self.assertIn('ALTER TABLE companies DROP INDEX domain_unique', statements)
        self.assertIn('ALTER TABLE companies ADD INDEX domain_lookup (domain)', statements)
        self.assertIn('ALTER TABLE companies ADD UNIQUE KEY source_url_unique (source_url(500))', statements)
        self.assertIn('dropped domain_unique', status)

    def test_duplicate_urls_get_a_plain_index_instead_of_losing_rows(self):
        cursor = self.cursor_for(['PRIMARY', 'domain_unique'], duplicates=2)
        status = ensure_unique_source_index(cursor)
        self.assertFalse(any('UNIQUE KEY' in sql for sql in self.statements(cursor)))
        self.assertIn('ALTER TABLE companies ADD INDEX source_url_lookup (source_url(500))', self.statements(cursor))
        self.assertIn('duplicate source URLs', status)

    def test_an_already_migrated_table_is_left_alone(self):
        cursor = self.cursor_for(['PRIMARY', 'domain_lookup', 'source_url_unique'])
        self.assertEqual(ensure_unique_source_index(cursor), '')
        self.assertEqual(len(cursor.statements), 1)


if __name__ == '__main__':
    unittest.main()
