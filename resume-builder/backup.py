"""Backups of everything Résumé Builder keeps: the saved résumés and cover letters (user-builds) and its data folder
(drafts, reference documents, references, rules, design and the uploaded résumé).

A snapshot is one ZIP in a folder outside both (resume-builder-backups, next to the Job Finder folders). Snapshots are taken
when the app starts, every hour while it runs, and just before anything is deleted or replaced. A snapshot is skipped when
nothing changed since the last one, and also when the data suddenly shrank to under half of the last snapshot, so a wipe can
never push the good copies out. The newest 5 are kept.

  python backup.py list                 show the snapshots
  python backup.py now                  take one now
  python backup.py restore <file.zip>   put a snapshot's files back (overwrites same-named files, deletes nothing)
"""
import hashlib
import json
import sys
import threading
import time
import zipfile
from datetime import datetime
from pathlib import Path

import config

KEEP = 5
STATE = "state.json"
SKIP_PARTS = ("data/fonts/", "data/current-resume.new/", "data/.secret_key")  # fonts re-download; the key is regenerated
_lock = threading.Lock()


def _files():
    """[(name inside the ZIP, path)] for everything worth keeping."""
    found = []
    for top, base in (("user-builds", config.OUTPUT_DIR), ("data", config.DATA_DIR)):
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*")):
            if not path.is_file():
                continue
            name = f"{top}/{path.relative_to(base).as_posix()}"
            if any(name.startswith(skip) or name == skip for skip in SKIP_PARTS):
                continue
            found.append((name, path))
    return found


def _fingerprint(files):
    digest = hashlib.sha1()
    for name, path in files:
        stat = path.stat()
        digest.update(f"{name}|{stat.st_size}|{stat.st_mtime_ns}\n".encode())
    return digest.hexdigest()


def _state():
    try:
        return json.loads((config.BACKUP_DIR / STATE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def list_snapshots():
    return sorted(config.BACKUP_DIR.glob("resume-builder-*.zip")) if config.BACKUP_DIR.is_dir() else []


def snapshot(reason="auto"):
    """Take a snapshot. Returns its path, or None when nothing changed, nothing is kept yet, or the data shrank suspiciously."""
    with _lock:
        files = _files()
        if not files:
            return None
        fingerprint, last = _fingerprint(files), _state()
        if fingerprint == last.get("fingerprint"):
            return None
        if last.get("count", 0) >= 8 and len(files) < last["count"] * 0.5:
            config.BACKUP_DIR.mkdir(parents=True, exist_ok=True)
            (config.BACKUP_DIR / "WARNING.txt").write_text(
                f"{datetime.now():%Y-%m-%d %H:%M}: Résumé Builder's files dropped from {last['count']} to {len(files)}. "
                "No new backup was made, so the earlier ones are safe. Restore with: python backup.py restore <file>\n", encoding="utf-8")
            print("Backup skipped: the files shrank a lot since the last snapshot. See WARNING.txt.", file=sys.stderr)
            return None
        config.BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        target = config.BACKUP_DIR / f"resume-builder-{datetime.now():%Y-%m-%d_%H-%M-%S}-{reason}.zip"
        temp = target.with_suffix(".tmp")
        with zipfile.ZipFile(temp, "w", zipfile.ZIP_DEFLATED) as archive:
            for name, path in files:
                try:
                    archive.write(path, name)
                except OSError:
                    continue  # a file being written right now: the next snapshot gets it
        temp.replace(target)
        (config.BACKUP_DIR / STATE).write_text(json.dumps({"fingerprint": fingerprint, "count": len(files), "file": target.name}), encoding="utf-8")
        (config.BACKUP_DIR / "WARNING.txt").unlink(missing_ok=True)
        for old in list_snapshots()[:-KEEP]:
            old.unlink(missing_ok=True)
        return target


def safely(reason):
    """snapshot() that never raises: a backup problem must not stop the app or the thing being done."""
    try:
        return snapshot(reason)
    except Exception as error:  # noqa: BLE001
        print(f"Backup failed: {error}", file=sys.stderr)
        return None


def start_hourly(seconds=3600):
    def loop():
        while True:
            time.sleep(seconds)
            safely("hourly")
    threading.Thread(target=loop, name="resume-builder-backup", daemon=True).start()


def restore(archive_path):
    """Put a snapshot's files back. Same-named files are replaced; nothing else is touched or deleted."""
    bases = {"user-builds": config.OUTPUT_DIR, "data": config.DATA_DIR}
    count = 0
    with zipfile.ZipFile(archive_path) as archive:
        for member in archive.namelist():
            top, _, rest = member.partition("/")
            if top not in bases or not rest or ".." in Path(rest).parts:
                continue
            destination = bases[top] / rest
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(archive.read(member))
            count += 1
    return count


if __name__ == "__main__":
    command = sys.argv[1] if len(sys.argv) > 1 else "list"
    if command == "now":
        print(snapshot("manual") or "Nothing new to back up.")
    elif command == "restore" and len(sys.argv) > 2:
        source = Path(sys.argv[2])
        source = source if source.is_file() else config.BACKUP_DIR / sys.argv[2]
        print(f"Restored {restore(source)} files from {source.name}.")
    else:
        for item in list_snapshots():
            print(f"{item.name}  {item.stat().st_size // 1024} KB")
        print(f"Folder: {config.BACKUP_DIR}")
