import gzip
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import no_database  # noqa: F401  (cuts tests off from the real database)
from jobfinder.web import log_rotation, search_control, webfiles
from unittest.mock import patch


class Rotation(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.log = Path(self.folder.name) / "job_finder.log"

    def tearDown(self):
        self.folder.cleanup()

    def archive(self, number):
        return self.log.with_name(f"job_finder.log.{number}.gz")

    def test_a_small_log_is_left_alone(self):
        self.log.write_text("one run\n", encoding="utf-8")
        self.assertFalse(log_rotation.rotate(self.log, max_bytes=100))
        self.assertEqual(self.log.read_text(encoding="utf-8"), "one run\n")
        self.assertFalse(self.archive(1).exists())

    def test_a_missing_log_is_fine(self):
        self.assertFalse(log_rotation.rotate(self.log))

    def test_a_large_log_is_compressed_and_starts_again(self):
        text = "Checking result 1: Web Designer\n" * 50
        self.log.write_text(text, encoding="utf-8")
        self.assertTrue(log_rotation.rotate(self.log, max_bytes=100))
        self.assertEqual(self.log.read_text(encoding="utf-8"), "")
        self.assertEqual(gzip.open(self.archive(1), "rt", encoding="utf-8").read(), text)
        self.assertLess(self.archive(1).stat().st_size, len(text))

    def test_only_the_newest_archives_are_kept(self):
        for number in range(1, 6):
            self.log.write_text(f"run {number}\n" * 40, encoding="utf-8")
            log_rotation.rotate(self.log, max_bytes=10)
        newest_first = [gzip.open(self.archive(n), "rt", encoding="utf-8").read().split("\n")[0] for n in (1, 2, 3)]
        self.assertEqual(newest_first, ["run 5", "run 4", "run 3"])
        self.assertFalse(self.archive(4).exists())

    def test_a_log_that_cannot_be_archived_is_left_as_it_is(self):
        self.log.write_text("x" * 50, encoding="utf-8")
        with patch.object(log_rotation.gzip, "open", side_effect=OSError("disk full")):
            self.assertFalse(log_rotation.rotate(self.log, max_bytes=10))
        self.assertEqual(self.log.read_text(encoding="utf-8"), "x" * 50)

    def test_a_run_adds_to_the_log_after_it_is_archived(self):
        self.log.write_text("old run\n" * 30, encoding="utf-8")
        with patch.object(webfiles, "SCRAPER_LOG_FILE", self.log), patch.object(log_rotation, "MAX_BYTES", 50):
            with search_control.open_run_log() as handle:
                handle.write("new run\n")
        self.assertEqual(self.log.read_text(encoding="utf-8"), "new run\n")
        self.assertTrue(self.archive(1).exists())


if __name__ == "__main__":
    unittest.main()
