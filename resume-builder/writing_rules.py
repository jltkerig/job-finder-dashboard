"""The user's own rules for writing, kept as two lists: one for resumes, one for cover letters.
Stored only in data/resume-rules.md and data/cover-letter-rules.md.

Claude reads them (get_writing_rules) before writing anything; they win over the connector's built-in rules.
"""
import config

KINDS = {"resume": "resume-rules.md", "cover_letter": "cover-letter-rules.md"}
TITLES = {"resume": "Résumé Rules", "cover_letter": "Cover Letter Rules"}
OLD_FILE = "writing-rules.md"  # one combined list, before the split
SPLIT_AT = "## Cover Letter Guidelines"
MAX_CHARS = 60_000


def _split_old_file():
    """An older single rules file becomes the two lists: everything from its cover-letter heading on is the
    cover letter's. The old file is kept, renamed, in case anything needs checking."""
    old = config.DATA_DIR / OLD_FILE
    if not old.exists() or any((config.DATA_DIR / name).exists() for name in KINDS.values()):
        return
    text = old.read_text(encoding="utf-8")
    resume, found, letter = text.partition(SPLIT_AT)
    save("resume", resume)
    save("cover_letter", (found + letter) if found else "")
    old.replace(old.with_suffix(".md.bak"))


def load(kind):
    _split_old_file()
    path = config.DATA_DIR / KINDS[kind]
    return path.read_text(encoding="utf-8") if path.exists() else ""


def save(kind, text):
    if kind not in KINDS:
        raise ValueError(f"Unknown rules: {kind}")
    text = (text or "").replace("\r\n", "\n").strip()
    if len(text) > MAX_CHARS:
        raise ValueError(f"Rules can be up to {MAX_CHARS:,} characters.")
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = config.DATA_DIR / KINDS[kind]
    temp = path.with_suffix(".tmp")
    temp.write_text(text + "\n" if text else "", encoding="utf-8")
    temp.replace(path)
