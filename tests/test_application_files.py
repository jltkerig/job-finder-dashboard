import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import no_database  # noqa: F401  (cuts tests off from the real database)
import dashboard
from jobfinder.web import application_files

ROOT = Path(__file__).resolve().parents[1]


class ResumesAndCoverLettersOnTheDashboard(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        base = Path(self.temp.name)
        self.drafts, self.output = base / "drafts", base / "user-builds"
        self.drafts.mkdir()
        self.output.mkdir()
        patcher = patch.multiple(application_files, DRAFTS_DIR=self.drafts, OUTPUT_DIR=self.output)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.temp.cleanup)

    def draft(self, pdf, kind, job_id, updated, make_pdf=True):
        (self.drafts / f"{Path(pdf).stem}.json").write_text(
            json.dumps({"pdf": pdf, "kind": kind, "job_id": job_id, "updated": updated}), encoding="utf-8")
        if make_pdf:
            (self.output / pdf).write_bytes(b"%PDF-1.4 test")

    def test_files_are_listed_under_their_job_newest_first(self):
        self.draft("Jane_Doe_Acme_Web_Designer_10-01-2026.pdf", "resume", 7, "2026-10-01T09:00:00")
        self.draft("Jane_Doe_Acme_Web_Designer_Cover_Letter_10-02-2026.pdf", "cover_letter", 7, "2026-10-02T09:00:00")
        self.draft("Jane_Doe_Other_10-02-2026.pdf", "resume", 8, "2026-10-02T09:00:00")
        self.draft("Jane_Doe_Gone_10-02-2026.pdf", "resume", 7, "2026-10-03T09:00:00", make_pdf=False)  # PDF deleted
        self.draft("Jane_Doe_General_10-02-2026.pdf", "resume", None, "2026-10-02T09:00:00")  # not for a job
        companies = application_files.add_application_files([{"id": 7}, {"id": 9}])
        self.assertEqual([f["label"] for f in companies[0]["application_files"]], ["Cover Letter", "Résumé"])
        self.assertEqual(companies[1]["application_files"], [])

    def test_only_saved_pdfs_can_be_opened(self):
        self.draft("Jane_Doe_Acme_10-02-2026.pdf", "resume", 7, "2026-10-02")
        client = dashboard.app.test_client()
        response = client.get("/user-builds/Jane_Doe_Acme_10-02-2026.pdf")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "application/pdf")
        response.close()
        self.assertEqual(client.get("/user-builds/missing.pdf").status_code, 404)
        self.assertEqual(client.get("/user-builds/..%5Csecret.pdf").status_code, 404)

    def test_the_card_shows_the_files_and_a_quiet_row_of_remove_actions(self):
        html = (ROOT / "templates" / "user-dashboard.html").read_text(encoding="utf-8")
        self.assertIn("Résumé and Cover Letter", html)
        self.assertIn('href="/user-builds/{{ file.pdf }}"', html)
        self.assertIn('class="card-footer-actions"', html)
        self.assertIn('class="bordered-button apply-action"', html)


if __name__ == "__main__":
    unittest.main()
