"""Persistent search decisions with short-lived skip caching."""
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path


TTL_HOURS = {
    "Directory or marketplace page": 24,
    "Article or student employment guide": 24,
    "No matching individual opening": 4,
}


def latest_decisions(path, limit=2000):
    records = {}
    try:
        with Path(path).open(encoding="utf-8") as stream:
            for line in stream:
                try:
                    event = json.loads(line)
                except (ValueError, TypeError):
                    continue
                url = event.get("url")
                if url:
                    records[url] = event
    except OSError:
        return {}
    return dict(list(records.items())[-limit:])


def cached_skip(records, url, now=None):
    event = records.get(url) or {}
    hours = TTL_HOURS.get(event.get("reason"))
    if not hours or not event.get("checked_at"):
        return None
    now = now or datetime.now(timezone.utc)
    try:
        checked = datetime.fromisoformat(event["checked_at"].replace("Z", "+00:00"))
        if checked.tzinfo is None:
            return None
        until = checked + timedelta(hours=hours)
        return event if now < until else None
    except (TypeError, ValueError, OverflowError):
        return None


def record_decision(path, records, reason, url, title=""):
    if not url:
        return
    event = {"reason": reason, "url": url, "title": title[:120],
             "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    records[url] = event
    path = Path(path)
    try:
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(event, ensure_ascii=False) + "\n")
        if path.stat().st_size > 2_000_000:
            temporary = path.with_name(path.name + ".tmp")
            with temporary.open("w", encoding="utf-8") as stream:
                for item in list(records.values())[-2000:]:
                    stream.write(json.dumps(item, ensure_ascii=False) + "\n")
            os.replace(temporary, path)
    except OSError:
        pass
