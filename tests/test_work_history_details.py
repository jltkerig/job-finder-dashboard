import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import no_database  # noqa: F401  (cuts tests off from the real database)
from jobfinder.web.schema import WORK_DETAIL_COLUMNS

ROOT = Path(__file__).resolve().parents[1]


class WorkHistoryDetails(unittest.TestCase):
    def test_every_optional_detail_has_a_box_on_the_dashboard(self):
        script = (ROOT / "static" / "js" / "charts.js").read_text(encoding="utf-8")
        for column, _ in WORK_DETAIL_COLUMNS:
            self.assertIn(f'"{column}"', script)
        self.assertIn('"Location and Contact"', script)
        self.assertIn('"Supervisor"', script)

    def test_the_same_columns_as_resume_builder(self):
        builder = ROOT / "resume-builder" / "jobfinder_db.py"
        if not builder.exists():
            self.skipTest("Résumé Builder is not inside Job Finder")
        text = builder.read_text(encoding="utf-8")
        for column, size in WORK_DETAIL_COLUMNS:
            self.assertIn(f'"{column}": {size}', text)


if __name__ == "__main__":
    unittest.main()