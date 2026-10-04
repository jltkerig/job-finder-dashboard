import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import no_database  # noqa: F401  (cuts tests off from the real database)
from jobfinder import form_examples

ROOT = Path(__file__).resolve().parents[1]


class FormExamples(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.file = Path(self.folder.name) / "form-examples.json"
        self.addCleanup(self.folder.cleanup)
        patcher = patch.object(form_examples, "FILE", self.file)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_no_file_means_no_examples(self):
        self.assertEqual(form_examples.examples(), {})

    def test_your_values_show_as_examples(self):
        self.file.write_text(json.dumps({"school": " Example University ", "gpa": "", "made_up_key": "x", "zip": 5}),
                             encoding="utf-8")
        self.assertEqual(form_examples.examples(), {"school": "e.g. Example University"})

    def test_a_broken_file_means_no_examples(self):
        self.file.write_text("{not json", encoding="utf-8")
        self.assertEqual(form_examples.examples(), {})
        self.file.write_text("[1, 2]", encoding="utf-8")
        self.assertEqual(form_examples.examples(), {})

    def test_the_forms_ship_without_personal_examples(self):
        # Every example on the Profile forms comes from form-examples.json, so the public code has none of its own.
        script = (ROOT / "static" / "js" / "charts.js").read_text(encoding="utf-8")
        for key in form_examples.KEYS:
            if key.startswith("home_"):
                continue  # those boxes are in the page templates
            self.assertIn(f'example("{key}")', script)
        dashboard = (ROOT / "templates" / "user-dashboard.html").read_text(encoding="utf-8")
        self.assertIn("form_examples.home_zip", dashboard)
        self.assertIn("form_examples.home_location", dashboard)


if __name__ == "__main__":
    unittest.main()
