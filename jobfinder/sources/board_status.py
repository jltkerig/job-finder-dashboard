"""Whether a job is still on the employer's own hiring board (Ashby, Greenhouse, Lever ...).

Used for jobs saved from LinkedIn and other sites that can't be revisited: their posting link goes to the employer's
board, and the board's own list of openings says if the job is gone. The posting page can't: these systems build it
with JavaScript, so a closed job's page still loads and reads as an empty shell."""

import time

import requests

from jobfinder.sources.ats_discovery import identify
from jobfinder.sources.employer_common import Http, _WholeBoard
from jobfinder.sources.employer_jobs import ADAPTERS, config_key

CACHE_SECONDS = 300  # many saved jobs share one board: ask it once, not once per job
_boards = {}  # board key -> (when read, ids of the jobs it lists, or None when it couldn't be read)


def clear_cache():
    _boards.clear()


def _listed_ids(adapter, key, http):
    saved = _boards.get(key)
    if saved and time.time() - saved[0] < CACHE_SECONDS:
        return saved[1]
    try:
        jobs = adapter._fetch(http)
    except (requests.RequestException, ValueError, KeyError, TypeError):
        jobs = []
    ids = {str(job.get("id")) for job in jobs} or None  # an empty board is more likely a hiccup than every job closing
    _boards[key] = (time.time(), ids)
    return ids


def board_status(url, http=None):
    """'Open', 'Closed' or 'Unknown' for a posting on an employer's hiring board; 'Unknown' for any other address."""
    config = identify(url)
    if not config or config.get("system") not in ADAPTERS:
        return "Unknown"
    http = http or Http()
    try:
        adapter = ADAPTERS[config["system"]](config)
        if isinstance(adapter, _WholeBoard):
            wanted = adapter._id_from(url)
            ids = _listed_ids(adapter, config_key(config), http) if wanted else None
            if ids is None:
                return "Unknown"
            return "Open" if wanted in ids else "Closed"
        return adapter.status(url, http)
    except (requests.RequestException, ValueError, KeyError, TypeError):
        return "Unknown"
