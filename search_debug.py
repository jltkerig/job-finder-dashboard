"""Rolling debug record of the last few runs: the inputs and why each lead was kept or skipped."""
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

MAX_RUNS = 10
MAX_SKIPS = 300
MAX_LEADS = 200
MAX_TEXT = 300
MAX_ITEMS = 60
_SECRET_KEY = re.compile(r"password|secret|token|api_?key", re.I)


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _clean(value, depth=0):
    """JSON-safe copy with long text clipped and anything secret-looking dropped."""
    if isinstance(value, str):
        return value if len(value) <= MAX_TEXT else value[:MAX_TEXT] + "…"
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    if depth > 6:
        return str(value)[:MAX_TEXT]
    if isinstance(value, dict):
        return {str(key): _clean(item, depth + 1) for key, item in value.items()
                if not _SECRET_KEY.search(str(key))}
    if isinstance(value, (list, tuple, set)):
        items = list(value)
        cleaned = [_clean(item, depth + 1) for item in items[:MAX_ITEMS]]
        if len(items) > MAX_ITEMS:
            cleaned.append(f"… {len(items) - MAX_ITEMS} more")
        return cleaned
    return _clean(str(value), depth)


class DebugRun:
    """One run's record; write() keeps only the newest MAX_RUNS runs in the file."""

    def __init__(self, path, mode, version):
        self.path = Path(path)
        self.stop_reason = None
        self._done = False
        self.data = {"mode": mode, "version": version, "started_at": _now(), "finished_at": None,
                     "stop_reason": None, "inputs": {}, "queries": [], "leads": [], "skips": [],
                     "skip_counts": {}, "notes": []}

    def set_inputs(self, **inputs):
        self.data["inputs"].update(_clean(inputs))

    def query(self, text):
        self.data["queries"].append({"query": _clean(text), "pages": 0, "results": 0})

    def query_page(self, results):
        if self.data["queries"]:
            self.data["queries"][-1]["pages"] += 1
            self.data["queries"][-1]["results"] += int(results)

    def lead(self, **fields):
        if len(self.data["leads"]) < MAX_LEADS:
            self.data["leads"].append(_clean(fields))

    def skip(self, reason, url, title=""):
        counts = self.data["skip_counts"]
        counts[reason] = counts.get(reason, 0) + 1
        if len(self.data["skips"]) < MAX_SKIPS:
            self.data["skips"].append({"reason": _clean(reason), "url": _clean(url), "title": _clean(title)})

    def note(self, text):
        if len(self.data["notes"]) < MAX_SKIPS:
            self.data["notes"].append(_clean(text))

    def finish(self, stop_reason=None):
        if self._done:
            return
        self._done = True
        self.data["finished_at"] = _now()
        self.data["stop_reason"] = _clean(stop_reason or self.stop_reason or "unknown")
        self.write()

    def write(self):
        runs = []
        try:
            existing = json.loads(self.path.read_text(encoding="utf-8"))
            runs = [run for run in existing.get("runs", []) if isinstance(run, dict)]
        except (OSError, ValueError, AttributeError):
            pass
        runs.append(self.data)
        payload = {"schema": 1, "note": f"Newest last; only the last {MAX_RUNS} runs are kept.",
                   "runs": runs[-MAX_RUNS:]}
        temporary = self.path.with_name(self.path.name + ".tmp")
        try:
            temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
            os.replace(temporary, self.path)
        except OSError:
            pass
