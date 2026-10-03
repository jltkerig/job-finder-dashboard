"""Résumés and cover letters Résumé Builder made for a saved job, shown on that job's Dashboard card.

Résumé Builder keeps a draft record for every PDF it saves (data/drafts/*.json, with the Job Finder job id) and
puts the PDF in this app's user-builds folder. Nothing here writes to either place.
"""

import json
import os
import re
from pathlib import Path

from flask import abort, send_from_directory

from jobfinder.web.core import app
from jobfinder.web.webfiles import BASE_DIR

RESUME_BUILDER_DIR = Path(os.getenv("RESUME_BUILDER_DIR", BASE_DIR.parent / "resume-builder"))
DRAFTS_DIR = RESUME_BUILDER_DIR / "data" / "drafts"
OUTPUT_DIR = Path(os.getenv("RESUME_OUTPUT_DIR", BASE_DIR / "user-builds"))
RESUME_BUILDER_URL = os.getenv("RESUME_BUILDER_URL", "http://127.0.0.1:5001")
SAFE_NAME = re.compile(r"^[A-Za-z0-9_.-]+\.pdf$")
KIND_LABELS = {"resume": "Résumé", "cover_letter": "Cover Letter"}


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
    return found


def add_application_files(companies):
    files = files_by_job()
    for company in companies:
        company["application_files"] = files.get(company.get("id"), [])
    return companies


@app.route("/user-builds/<name>")
def user_build_file(name):
    """One of your résumé or cover letter PDFs, opened in the browser."""
    if not SAFE_NAME.match(name) or not (OUTPUT_DIR / name).is_file():
        abort(404)
    return send_from_directory(OUTPUT_DIR, name, mimetype="application/pdf", max_age=0)
