"""Saving finished resumes and cover letters to Job Finder's user-builds folder.

Each PDF has a matching draft (the content and layout it was drawn from) in data/drafts,
so it can be edited in the web app and drawn again.
"""
import json
import re
from datetime import date, datetime
from pathlib import Path

import config
import resume_file
import design
from models import CoverLetterContent, ResumeContent, merge_layout
from pdf_render import render_cover_letter, render_resume

KINDS = {"resume": ("", ResumeContent, render_resume),
         "cover_letter": ("Cover_Letter", CoverLetterContent, render_cover_letter)}
SAFE_NAME = re.compile(r"^[A-Za-z0-9_.-]+\.pdf$")


def _part(text):
    return re.sub(r"[^A-Za-z0-9]+", "_", text or "").strip("_")[:60]


def build_filename(first, last, kind, job_title, company="", when=None):
    """Firstname_Lastname_Company_JobTitle_MM-DD-YYYY.pdf for a resume (never the word "ATS"), and
    Firstname_Lastname_Company_JobTitle_Cover_Letter_MM-DD-YYYY.pdf for a cover letter; _2, _3 ... if the name is taken."""
    label = KINDS[kind][0]
    parts = [_part(first), _part(last), _part(company), _part(job_title) or "General", label,
             (when or date.today()).strftime("%m-%d-%Y")]
    stem = "_".join(p for p in parts if p)
    stem = re.sub(r"(?i)(?:^|_)ATS(?=_|$)", "", stem).strip("_")
    name, n = f"{stem}.pdf", 2
    while (config.OUTPUT_DIR / name).exists() or (config.DRAFTS_DIR / f"{Path(name).stem}.json").exists():
        name, n = f"{stem}_{n}.pdf", n + 1
    return name


def split_name(full_name, profile):
    first, last = (profile or {}).get("first_name", ""), (profile or {}).get("last_name", "")
    if first or last:
        return first, last
    words = (full_name or "").split()
    return (words[0], " ".join(words[1:])) if words else ("", "")


def save_build(kind, content, *, job_title="", company="", job_id=None, layout=None, profile=None, replace=""):
    """Draw the PDF and store its draft. Returns the draft record."""
    if kind not in KINDS:
        raise ValueError(f"Unknown kind: {kind}")
    content = KINDS[kind][1].model_validate(content)
    config.ensure_dirs()
    info = resume_file.current_info() or {}
    merged = merge_layout(info.get("layout"), design.overrides(), layout)
    if replace:
        existing = load_draft(replace)
        filename = existing["pdf"]
        created = existing.get("created")
    else:
        first, last = split_name(content.full_name, profile)
        filename = build_filename(first, last, kind, job_title, company)
        created = datetime.now().isoformat(timespec="seconds")
    pdf_path = config.OUTPUT_DIR / filename
    temp_path = pdf_path.with_suffix(".tmp")
    KINDS[kind][2](content, merged, temp_path)
    temp_path.replace(pdf_path)
    record = {
        "kind": kind, "pdf": filename, "job_id": job_id, "job_title": job_title, "company": company,
        "created": created, "updated": datetime.now().isoformat(timespec="seconds"),
        "content": content.model_dump(), "layout": merged,
    }
    _draft_path(filename).write_text(json.dumps(record, indent=2), encoding="utf-8")
    return record


def _draft_path(filename):
    if not SAFE_NAME.match(filename or ""):
        raise ValueError("Not a Résumé Builder file name.")
    return config.DRAFTS_DIR / f"{Path(filename).stem}.json"


def load_draft(filename):
    path = _draft_path(filename)
    if not path.exists():
        raise FileNotFoundError(f"No draft for {filename}.")
    return json.loads(path.read_text(encoding="utf-8"))


def list_builds():
    config.ensure_dirs()
    records = []
    for path in config.DRAFTS_DIR.glob("*.json"):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            continue
        record["pdf_exists"] = (config.OUTPUT_DIR / record.get("pdf", "")).exists()
        records.append(record)
    return sorted(records, key=lambda r: r.get("updated", ""), reverse=True)


def delete_build(filename):
    pdf_path(filename).unlink(missing_ok=True)
    _draft_path(filename).unlink(missing_ok=True)


def pdf_path(filename):
    _draft_path(filename)  # validates the name
    return config.OUTPUT_DIR / filename
