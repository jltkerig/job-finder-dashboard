import json
import os
import shutil
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
        self.assertNotIn("attachment", response.headers.get("Content-Disposition", ""))
        response.close()
        saved = client.get("/user-builds/Jane_Doe_Acme_10-02-2026.pdf?download=1")
        self.assertIn("attachment", saved.headers["Content-Disposition"])
        self.assertEqual(saved.mimetype, "application/octet-stream")  # never opened in a browser's PDF viewer
        self.assertIn("Jane_Doe_Acme_10-02-2026.pdf", saved.headers["Content-Disposition"])
        saved.close()
        self.assertEqual(client.get("/user-builds/missing.pdf").status_code, 404)
        self.assertEqual(client.get("/user-builds/..%5Csecret.pdf").status_code, 404)

    def real_pdf(self, name, pages):
        from pypdf import PdfWriter
        writer = PdfWriter()
        for _ in range(pages):
            writer.add_blank_page(width=612, height=792)
        with (self.output / name).open("wb") as handle:
            writer.write(handle)

    def test_combine_puts_the_resume_then_the_cover_letter_in_one_pdf(self):
        from pypdf import PdfReader
        resume, cover = "Jane_Doe_Acme_Web_Designer_10-02-2026.pdf", "Jane_Doe_Acme_Web_Designer_Cover_Letter_10-02-2026.pdf"
        self.draft(resume, "resume", 7, "2026-10-02", make_pdf=False)
        self.draft(cover, "cover_letter", 7, "2026-10-02", make_pdf=False)
        self.real_pdf(resume, 1)
        self.real_pdf(cover, 2)
        client = dashboard.app.test_client()
        with client.session_transaction() as session:
            session["csrf_token"] = "token"
        response = client.post("/apply-queue/combine/7", headers={"X-CSRF-Token": "token"})
        self.assertEqual(response.status_code, 200)
        combined = "Jane_Doe_Acme_Web_Designer_Resume_and_Cover_Letter_10-02-2026.pdf"
        self.assertEqual(response.get_json()["pdf"], combined)
        self.assertEqual(len(PdfReader(str(self.output / combined)).pages), 3)  # 1 résumé page + 2 cover letter pages
        self.assertEqual([p.name for p in self.output.glob("*.tmp")], [])
        listed = application_files.files_by_job()[7]
        self.assertEqual([f["kind"] for f in listed if f["kind"] == "combined"], ["combined"])
        companies = application_files.add_application_files([{"id": 7}])
        self.assertTrue(companies[0]["can_combine"])
        self.assertEqual(companies[0]["combined_pdf"], combined)
        # Asking again replaces it rather than adding another file.
        self.assertEqual(client.post("/apply-queue/combine/7", headers={"X-CSRF-Token": "token"}).status_code, 200)
        self.assertEqual(len(list(self.output.glob("*Resume_and_Cover_Letter*.pdf"))), 1)

    def test_combine_needs_both_documents(self):
        self.draft("Jane_Doe_Acme_10-02-2026.pdf", "resume", 8, "2026-10-02")
        client = dashboard.app.test_client()
        with client.session_transaction() as session:
            session["csrf_token"] = "token"
        response = client.post("/apply-queue/combine/8", headers={"X-CSRF-Token": "token"})
        self.assertEqual(response.status_code, 400)
        self.assertFalse(application_files.add_application_files([{"id": 8}])[0]["can_combine"])

    def test_the_save_as_window_copies_the_pdf_where_you_choose(self):
        self.draft("Jane_Doe_Acme_10-02-2026.pdf", "resume", 7, "2026-10-02")
        client = dashboard.app.test_client()
        with client.session_transaction() as session:
            session["csrf_token"] = "token"
        target = Path(tempfile.mkdtemp()) / "My Resume.pdf"
        self.addCleanup(lambda: shutil.rmtree(target.parent, ignore_errors=True))
        post = lambda body: client.post("/user-builds/save-as", json=body, headers={"X-CSRF-Token": "token"})
        with patch.object(application_files.save_dialog, "available", return_value=True),                 patch.object(application_files.save_dialog, "ask_save_path", return_value=target) as ask:
            response = post({"names": ["Jane_Doe_Acme_10-02-2026.pdf"]})
            self.assertEqual(response.get_json(), {"saved": [str(target)], "cancelled": False})
            self.assertEqual(ask.call_args[0][0], "Jane_Doe_Acme_10-02-2026.pdf")
            self.assertTrue(target.is_file())
            self.assertEqual(post({"names": ["../secret.pdf"]}).status_code, 400)
            self.assertEqual(post({"names": ["missing.pdf"]}).status_code, 400)
        with patch.object(application_files.save_dialog, "available", return_value=True),                 patch.object(application_files.save_dialog, "ask_save_path", return_value=None):
            self.assertEqual(post({"names": ["Jane_Doe_Acme_10-02-2026.pdf"]}).get_json(), {"saved": [], "cancelled": True})
        with patch.object(application_files.save_dialog, "available", return_value=False):
            self.assertEqual(post({"names": ["Jane_Doe_Acme_10-02-2026.pdf"]}).status_code, 501)

    def test_the_card_shows_the_files_and_a_quiet_row_of_remove_actions(self):
        html = (ROOT / "templates" / "user-dashboard.html").read_text(encoding="utf-8")
        self.assertIn("Résumé and Cover Letter", html)
        self.assertIn('href="/user-builds/{{ file.pdf }}"', html)
        self.assertIn('class="card-footer-actions"', html)
        self.assertIn('class="bordered-button apply-action"', html)


class AssetVersion(unittest.TestCase):
    def test_an_edited_static_file_gets_a_new_version_without_a_version_bump(self):
        from jobfinder.web import core
        folder = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: shutil.rmtree(folder, ignore_errors=True))
        script = folder / "app.js"
        script.write_text("1", encoding="utf-8")
        first = core.asset_version(folder)
        os.utime(script, (script.stat().st_atime, script.stat().st_mtime + 100))
        second = core.asset_version(folder)
        self.assertTrue(first.startswith(core.app.config["APP_VERSION"] + "."))
        self.assertNotEqual(first, second)


if __name__ == "__main__":
    unittest.main()
