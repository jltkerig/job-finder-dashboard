"""What each employer board and remote feed did in the most recent search (board_health.json).

The search notes one entry per board; the dashboard's Tuning page shows them, so nobody has to read the log
to learn that a board was blocked or produced nothing.
"""
import json
import os
import re
import threading
from datetime import datetime, timezone
from pathlib import Path

from jobfinder import paths

HEALTH_FILE = paths.BOARD_HEALTH_FILE

# status -> what it means for the reader
STATUSES = {"ok": "Working", "no matches": "Working, no matching titles", "blocked": "Blocked", "down": "Down",
            "off": "Turned off"}


def classify_error(error):
    """'blocked' when the site refused us (403, 401, 429, bot wall), otherwise 'down'."""
    text = str(error)
    if re.search(r"\b(?:401|403|429)\b|not authorized|forbidden|captcha|too many requests", text, re.I):
        return "blocked"
    return "down"


class BoardHealth:
    def __init__(self):
        self._boards = {}
        self._lock = threading.Lock()

    def note(self, name, kind, status, matches=0, saved=0, detail=""):
        with self._lock:
            self._boards[(kind, name)] = {"name": name, "kind": kind, "status": status, "matches": int(matches),
                                          "saved": int(saved), "detail": str(detail)[:200]}

    def failed(self, name, kind, error):
        self.note(name, kind, classify_error(error), detail=error)

    def clear(self):
        with self._lock:
            self._boards.clear()

    def boards(self):
        with self._lock:
            return sorted(self._boards.values(), key=lambda item: (item["kind"], item["name"].casefold()))

    def write(self, path=HEALTH_FILE, run="search"):
        """Save the run's boards. A run that noted nothing (an early stop) keeps the previous file."""
        boards = self.boards()
        if not boards:
            return
        payload = {"updated": datetime.now(timezone.utc).isoformat(timespec="seconds"), "run": run, "boards": boards}
        temporary = Path(str(path) + ".tmp")
        try:
            temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
            os.replace(temporary, path)
        except OSError:
            pass


def read_health(path=HEALTH_FILE):
    """The saved report as {"updated", "run", "boards"}, or None when no search has recorded one."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and isinstance(data.get("boards"), list) else None
