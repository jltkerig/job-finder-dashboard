"""The pages the last searches skipped and why (shown on the Tuning page)."""

from jobfinder.records.search_skips import latest_decisions
from jobfinder.sources.job_listings import is_pdf_url
from jobfinder.web.webfiles import SEARCH_SKIPS_FILE


def recent_skipped(limit=20):
    """The latest skipped pages, newest first: [{"reason", "title", "url"}]. [] when there are none or the record is unreadable."""
    skipped = []
    if not SEARCH_SKIPS_FILE.exists():
        return skipped
    try:
        for item in reversed(list(latest_decisions(SEARCH_SKIPS_FILE).values())):
            # Older searches recorded PDFs as skips; they are now ignored entirely.
            if item.get("reason") != "Passed" and not is_pdf_url(item.get("url")):
                skipped.append(item)
            if len(skipped) >= limit:
                break
    except (OSError, ValueError):
        return []
    return skipped
