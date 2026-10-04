"""Reference documents: past cover letters, reviews, certificates, project lists...

Each upload lives in data/documents/<id>/ with the original file and its extracted text.
data/documents/index.json lists them with the user's label.
"""
import json
import secrets
import shutil
from datetime import datetime
from pathlib import Path

import config
import resume_file

ALLOWED = {".pdf", ".docx", ".txt", ".md"}
MAX_DOCUMENTS = 40
MAX_TEXT_CHARS = 60_000  # a long document is cut here so one file can't swamp Claude's context


class DocumentError(ValueError):
    pass


def _root():
    return config.DATA_DIR / "documents"


def _index_path():
    return _root() / "index.json"


def load():
    path = _index_path()
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else []


def _write(rows):
    _root().mkdir(parents=True, exist_ok=True)
    temp = _index_path().with_suffix(".tmp")
    temp.write_text(json.dumps(rows, indent=2), encoding="utf-8")
    temp.replace(_index_path())


def _docx_text(path):
    import docx

    document = docx.Document(str(path))
    lines = [p.text for p in document.paragraphs if p.text.strip()]
    for table in document.tables:
        for row in table.rows:
            cells = [cell.text.strip() for cell in row.cells if cell.text.strip()]
            if cells:
                lines.append(" | ".join(cells))
    return "\n".join(lines)


def _plain_text(data):
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def add(filename, data, label=""):
    suffix = Path(filename or "").suffix.lower()
    if suffix not in ALLOWED:
        raise DocumentError("Upload a PDF, Word .docx, or a .txt / .md text file.")
    if len(data) > config.MAX_UPLOAD_BYTES:
        raise DocumentError("That file is larger than 10 MB.")
    if suffix in (".pdf", ".docx"):
        try:
            resume_file._check_signature(data, suffix)
        except resume_file.ResumeFileError as error:
            raise DocumentError(str(error)) from error
    rows = load()
    if len(rows) >= MAX_DOCUMENTS:
        raise DocumentError(f"You already have {MAX_DOCUMENTS} documents. Delete one first.")

    doc_id = secrets.token_hex(6)
    folder = _root() / doc_id
    folder.mkdir(parents=True)
    original = folder / f"original{suffix}"
    original.write_bytes(data)
    pages = None
    try:
        if suffix == ".pdf":
            text = resume_file.pdf_text(original)
            import pymupdf
            with pymupdf.open(original) as pdf:
                pages = pdf.page_count
        elif suffix == ".docx":
            text = _docx_text(original)
        else:
            text = _plain_text(data)
    except Exception as error:
        shutil.rmtree(folder, ignore_errors=True)
        raise DocumentError(f"Could not read that file: {error}") from error
    text = text.strip()
    (folder / "text.txt").write_text(text, encoding="utf-8")

    record = {
        "id": doc_id,
        "label": (label or "").strip()[:120] or Path(filename).stem[:120],
        "original_name": Path(filename).name[:200],
        "type": suffix.lstrip(".").upper(),
        "file": original.name,
        "pages": pages,
        "chars": len(text),
        "uploaded_at": datetime.now().isoformat(timespec="seconds"),
    }
    _write(rows + [record])
    return record


def _find(doc_id):
    for row in load():
        if row["id"] == doc_id:
            return row
    raise KeyError(doc_id)


def rename(doc_id, label):
    rows = load()
    for row in rows:
        if row["id"] == doc_id:
            row["label"] = (label or "").strip()[:120] or row["label"]
            _write(rows)
            return row
    raise KeyError(doc_id)


def delete(doc_id):
    rows = load()
    kept = [r for r in rows if r["id"] != doc_id]
    if len(kept) == len(rows):
        raise KeyError(doc_id)
    _write(kept)
    shutil.rmtree(_root() / doc_id, ignore_errors=True)


def text(doc_id):
    _find(doc_id)
    path = _root() / doc_id / "text.txt"
    return path.read_text(encoding="utf-8") if path.exists() else ""


def page_images(doc_id, max_pages=2):
    """Pictures of a PDF's first pages, for scans and certificates that have no text."""
    row = _find(doc_id)
    if row["type"] != "PDF":
        return []
    return resume_file.page_images(_root() / doc_id / row["file"], max_pages=max_pages)


def file_path(doc_id):
    row = _find(doc_id)
    return _root() / doc_id / row["file"], row
