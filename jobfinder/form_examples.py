"""Your own examples for the empty boxes on the Profile forms, read from user-data/form-examples.json.

The code is public, so it ships with no personal examples: a box shows "e.g. <your value>" only when this file
has one, and stays blank otherwise. Job Finder's Dashboard and Résumé Builder both read it.
"""

import json

from jobfinder import paths

FILE = paths.USER_DIR / "form-examples.json"
KEYS = ("home_location", "home_zip", "work_company", "work_dates", "work_street", "work_city", "work_state", "work_zip",
        "work_phone", "work_website", "supervisor_name", "supervisor_title", "school", "major", "minor", "gpa")


def examples():
    """{key: "e.g. value"} for each key the file fills in; a missing or broken file gives {}."""
    try:
        saved = json.loads(FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(saved, dict):
        return {}
    found = {}
    for key in KEYS:
        value = saved.get(key)
        if isinstance(value, str) and value.strip():
            found[key] = "e.g. " + value.strip()[:120]
    return found
