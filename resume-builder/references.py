"""The user's references: people who agreed to vouch for them. Stored only in data/references.json."""
import json
import secrets

import config
from models import Reference

FILE_NAME = "references.json"


def load():
    path = config.DATA_DIR / FILE_NAME
    if not path.exists():
        return []
    rows = [Reference.model_validate(r).model_dump() for r in json.loads(path.read_text(encoding="utf-8"))]
    if any(not r["id"] for r in rows):  # older files had no ids
        for r in rows:
            r["id"] = r["id"] or secrets.token_hex(6)
        _write(rows)
    return rows


def _clean(row):
    values = {k: str(row.get(k) or "").strip()[:300] for k in Reference.model_fields if k != "id"}
    return Reference.model_validate(values).model_dump()


def _write(rows):
    config.ensure_dirs()
    path = config.DATA_DIR / FILE_NAME
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(rows, indent=2), encoding="utf-8")
    temp.replace(path)


def has_contact(reference):
    return bool(reference["name"] or reference["phone"] or reference["email"])


def add(row):
    reference = _clean(row)
    if not has_contact(reference):
        raise ValueError("Enter at least a name, phone or email.")
    reference["id"] = secrets.token_hex(6)
    _write(load() + [reference])
    return reference


def update(ref_id, row):
    reference = _clean(row)
    if not has_contact(reference):
        raise ValueError("Enter at least a name, phone or email.")
    rows = load()
    for i, existing in enumerate(rows):
        if existing["id"] == ref_id:
            reference["id"] = ref_id
            rows[i] = reference
            _write(rows)
            return reference
    raise KeyError(ref_id)


def delete(ref_id):
    rows = load()
    kept = [r for r in rows if r["id"] != ref_id]
    if len(kept) == len(rows):
        raise KeyError(ref_id)
    _write(kept)
