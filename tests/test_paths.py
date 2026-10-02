import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import no_database  # noqa: F401  (tests use a temporary user-data folder)
from jobfinder import paths


class UserFiles(unittest.TestCase):
    def setUp(self):
        self.base = Path(tempfile.mkdtemp(prefix="jf-paths-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(self.base, ignore_errors=True))
        (self.base / "data" / "defaults").mkdir(parents=True)
        for name, text in {"settings.json": "{}", "blocked_domains.txt": "default.example\n", "blocked_companies.txt": "",
                           "blocked_country_domains.txt": "", "watched_employers.json": "[]"}.items():
            (self.base / "data" / "defaults" / name).write_text(text, encoding="utf-8")

    def test_files_older_versions_kept_at_the_top_move_into_user_data(self):
        (self.base / "settings.json").write_text('{"mine": true}', encoding="utf-8")
        (self.base / "search_skips.jsonl").write_text("one\n", encoding="utf-8")
        (self.base / "feed_cache").mkdir()
        (self.base / "feed_cache" / "x.json").write_text("{}", encoding="utf-8")
        (self.base / "job_finder.log").write_text("log", encoding="utf-8")
        paths.ensure_user_files(self.base)
        self.assertEqual((self.base / "user-data" / "settings.json").read_text(encoding="utf-8"), '{"mine": true}')  # yours, not the default
        self.assertTrue((self.base / "user-data" / "search_skips.jsonl").exists())
        self.assertTrue((self.base / "user-data" / "feed-cache" / "x.json").exists())
        self.assertTrue((self.base / "logs" / "job_finder.log").exists())
        self.assertFalse((self.base / "settings.json").exists())

    def test_a_missing_list_starts_from_its_default(self):
        paths.ensure_user_files(self.base)
        self.assertEqual((self.base / "user-data" / "blocked_domains.txt").read_text(encoding="utf-8"), "default.example\n")

    def test_nothing_in_user_data_is_ever_overwritten(self):
        paths.ensure_user_files(self.base)
        mine = self.base / "user-data" / "blocked_domains.txt"
        mine.write_text("mine.example\n", encoding="utf-8")
        (self.base / "blocked_domains.txt").write_text("old.example\n", encoding="utf-8")  # an old copy turns up again
        paths.ensure_user_files(self.base)
        self.assertEqual(mine.read_text(encoding="utf-8"), "mine.example\n")

    def test_every_path_is_inside_the_project(self):
        for name in dir(paths):
            value = getattr(paths, name)
            if name.isupper() and isinstance(value, Path) and not value.is_relative_to(paths.USER_DIR) and value != paths.LOG_DIR                     and paths.LOG_DIR not in value.parents:  # user-data and logs may be moved by the environment (the tests do)
                self.assertIn(paths.ROOT, [value, *value.parents], name)

    def test_the_shipped_defaults_exist(self):
        for name in ("settings.json", "blocked_domains.txt", "blocked_companies.txt", "blocked_country_domains.txt", "watched_employers.json"):
            self.assertTrue((paths.DEFAULTS_DIR / name).exists(), name)
        self.assertTrue(paths.RECOMMENDED_DOMAINS_FILE.exists())
        self.assertTrue(paths.ONET_DIR.exists() and paths.PLACES_FILE.exists() and paths.ZIPS_FILE.exists())


if __name__ == "__main__":
    unittest.main()
