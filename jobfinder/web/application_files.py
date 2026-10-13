"""Résumés and cover letters Résumé Builder made for a saved job, shown on that job's Dashboard card.

Résumé Builder keeps a draft record for every PDF it saves (data/drafts/*.json, with the Job Finder job id) and
puts the PDF in this app's user-builds folder. Nothing here writes to either place.
"""

import json
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

from flask import abort, jsonify, request, send_from_directory

from jobfinder.web import save_dialog
from jobfinder.web.core import api_error, app
from jobfinder.web.webfiles import BASE_DIR

RESUME_BUILDER_DIR = Path(os.getenv("RESUME_BUILDER_DIR", BASE_DIR / "resume-builder"))
DRAFTS_DIR = RESUME_BUILDER_DIR / "data" / "drafts"
OUTPUT_DIR = Path(os.getenv("RESUME_OUTPUT_DIR", BASE_DIR / "user-builds"))
RESUME_BUILDER_URL = os.getenv("RESUME_BUILDER_URL", "http://127.0.0.1:5001")
SAFE_NAME = re.compile(r"^[A-Za-z0-9_.-]+\.pdf$")
KIND_LABELS = {"resume": "Résumé", "cover_letter": "Cover Letter", "combined": "Combined"}


def files_by_job():
    """{job id: [{"kind", "label", "pdf", "updated"}, ...]} for PDFs that still exist, newest first."""
    found = {}
    if not DRAFTS_DIR.is_dir():
        return found
    for path in DRAFTS_DIR.glob("*.json"):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
            job_id = int(record.get("job_id"))
        except (OSError, ValueError, TypeError):
            continue
        pdf = str(record.get("pdf") or "")
        if not SAFE_NAME.match(pdf) or not (OUTPUT_DIR / pdf).is_file():
            continue
        kind = record.get("kind", "")
        found.setdefault(job_id, []).append({"kind": kind, "label": KIND_LABELS.get(kind, "Document"), "pdf": pdf,
                                             "updated": str(record.get("updated") or "")})
    for files in found.values():
        files.sort(key=lambda f: f["updated"], reverse=True)
        pair = newest_pair(files)
        if pair and (OUTPUT_DIR / combined_name(*pair)).is_file():  # made earlier with Combine on the Apply Queue
            name = combined_name(*pair)
            modified = datetime.fromtimestamp((OUTPUT_DIR / name).stat().st_mtime, timezone.utc).isoformat()
            files.append({"kind": "combined", "label": KIND_LABELS["combined"], "pdf": name, "updated": modified})
    return found


def newest_pair(files):
    """(résumé PDF, cover letter PDF) names of a job's newest of each, or None when it lacks one."""
    resume = next((f["pdf"] for f in files if f["kind"] == "resume"), None)
    cover = next((f["pdf"] for f in files if f["kind"] == "cover_letter"), None)
    return (resume, cover) if resume and cover else None


def combined_name(resume_pdf, cover_pdf):
    """The one-PDF name for a résumé and its cover letter, e.g. ..._Cover_Letter_... becomes
    ..._Resume_and_Cover_Letter_..."""
    if "_Cover_Letter" in cover_pdf:
        name = cover_pdf.replace("_Cover_Letter", "_Resume_and_Cover_Letter", 1)
    else:
        name = f"{Path(resume_pdf).stem}_Resume_and_Cover_Letter.pdf"
    return name if SAFE_NAME.match(name) else "Resume_and_Cover_Letter.pdf"


def add_application_files(companies):
    files = files_by_job()
    for company in companies:
        company["application_files"] = files.get(company.get("id"), [])
        pair = newest_pair(company["application_files"])
        company["can_combine"] = bool(pair)
        company["combined_pdf"] = next((f["pdf"] for f in company["application_files"] if f["kind"] == "combined"), "")
        try:
            details = json.loads(company.get("listing_details") or "{}")
        except (TypeError, ValueError):
            details = {}
        company["apply_queued"] = bool(isinstance(details, dict) and details.get("apply_queued"))
    return companies


@app.route("/user-builds/<name>")
def user_build_file(name):
    """One of your résumé or cover letter PDFs: opened in the browser, or saved when asked with ?download=1."""
    if not SAFE_NAME.match(name) or not (OUTPUT_DIR / name).is_file():
        abort(404)
    if request.args.get("download") == "1":
        # A generic type, so no browser opens it in its own PDF viewer: it is saved.
        return send_from_directory(OUTPUT_DIR, name, mimetype="application/octet-stream", max_age=0,
                                   as_attachment=True, download_name=name)
    return send_from_directory(OUTPUT_DIR, name, mimetype="application/pdf", max_age=0)


@app.route("/apply-queue/combine/<int:company_id>", methods=["POST"])
def combine_application_files(company_id):
    """Put a job's résumé and cover letter into one PDF (résumé first), saved beside them in user-builds. The
    originals are not touched; asking again after either changes makes a fresh one."""
    pair = newest_pair(files_by_job().get(company_id, []))
    if not pair:
        return api_error("E3270", "This job needs both a résumé and a cover letter first.", 400)
    name = combined_name(*pair)
    target, temporary = OUTPUT_DIR / name, OUTPUT_DIR / (name + ".tmp")
    try:
        from pypdf import PdfWriter
        writer = PdfWriter()
        for pdf in pair:
            writer.append(str(OUTPUT_DIR / pdf))
        with temporary.open("wb") as handle:
            writer.write(handle)
        temporary.replace(target)
    except Exception as error:  # an unreadable PDF or a full disk: say so, leave the originals alone
        temporary.unlink(missing_ok=True)
        return api_error("E3271", f"Could not combine the files: {error}", 500)
    return jsonify({"pdf": name, "label": KIND_LABELS["combined"], "url": f"/user-builds/{name}"})


@app.route("/user-builds/save-as", methods=["POST"])
def save_builds_as():
    """Show the system's Save As window for each named PDF and copy it where you choose. This browser cannot do it
    (Firefox has no Save As prompt for web pages); Job Finder runs on your computer, so it asks itself."""
    names = (request.get_json(silent=True) or {}).get("names")
    if (not isinstance(names, list) or not 0 < len(names) <= 4
            or not all(isinstance(name, str) and SAFE_NAME.match(name) and (OUTPUT_DIR / name).is_file()
                       for name in names)):
        return api_error("E3260", "Choose the saved résumé or cover letter to save.", 400)
    if not save_dialog.available():
        return api_error("E3261", "The Save As window is not available here.", 501)
    saved = []
    try:
        for name in names:
            target = save_dialog.ask_save_path(name, Path.home() / "Desktop")
            if target is None:
                break  # cancelled: the rest are not asked about
            shutil.copyfile(OUTPUT_DIR / name, target)
            saved.append(str(target))
    except OSError as error:
        return api_error("E3262", f"Could not save the file: {error}", 500)
    return jsonify({"saved": saved, "cancelled": len(saved) < len(names)})
