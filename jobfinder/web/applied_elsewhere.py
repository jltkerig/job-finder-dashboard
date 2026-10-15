"""Applications made outside Job Finder: a résumé sent before Job Finder tracked applications, or on a site it never saw.

They are kept in user-data/applied_elsewhere.json as [{"company": "Acme", "title": "Web Designer", "applied_on":
"2026-09-23"}, ...] and counted in the Dashboard's Job Apply Stats (Applied In Total, Applied, and Applied By Month).
They are not saved jobs, so no other page lists them. An entry without a real date is ignored.
"""

import json
from pathlib import Path

from jobfinder import paths
from jobfinder.web.applied_dates import clean


def load(path=None):
    """The entries, each {"company", "title", "applied_on"}; [] when there is no file or it cannot be read."""
    try:
        data = json.loads(Path(path or paths.APPLIED_ELSEWHERE_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    entries = []
    for item in data if isinstance(data, list) else []:
        if not isinstance(item, dict):
            continue
        applied_on = clean(item.get("applied_on"))
        if applied_on:
            entries.append({"company": str(item.get("company") or "").strip()[:150],
                            "title": str(item.get("title") or "").strip()[:255], "applied_on": applied_on})
    return entries
