"""What the employer's own hiring board (Ashby, Greenhouse, Lever ...) says about a job: whether it is still listed, and
whether it is remote, hybrid or onsite.

Used for jobs saved from LinkedIn and other sites that can't be revisited: their posting link goes to the employer's
board, and the board's own list of openings says if the job is gone. The posting page can't: these systems build it
with JavaScript, so a closed job's page still loads and reads as an empty shell. The same list says how the job is
worked, which the page's data gets wrong (Ashby marks hybrid jobs TELECOMMUTE)."""

import time

import requests

from jobfinder.sources.ats_discovery import identify
from jobfinder.sources.employer_common import Http, _WholeBoard
from jobfinder.sources.employer_jobs import ADAPTERS, config_key

CACHE_SECONDS = 300  # many saved jobs share one board: ask it once, not once per job
_boards = {}  # board key -> (when read, {job id: the board's record of it}, or None when it couldn't be read)


def clear_cache():
    _boards.clear()


def _listed_jobs(adapter, key, http):
    """{id: record} of the jobs a board lists, or None when it couldn't be read."""
    saved = _boards.get(key)
    if saved and time.time() - saved[0] < CACHE_SECONDS:
        return saved[1]
    try:
        jobs = adapter._fetch(http)
    except (requests.RequestException, ValueError, KeyError, TypeError):
        jobs = []
    listed = {str(job.get("id")): job for job in jobs} or None  # an empty board is more likely a hiccup than every job closing
    _boards[key] = (time.time(), listed)
    return listed


def _board_job(url, http):
    """(adapter, the board's record of this job or None, whether the board could be read) for a posting address."""
    config = identify(url)
    if not config or config.get("system") not in ADAPTERS:
        return None, None, False
    adapter = ADAPTERS[config["system"]](config)
    if not isinstance(adapter, _WholeBoard):
        return adapter, None, False
    wanted = adapter._id_from(url)
    listed = _listed_jobs(adapter, config_key(config), http or Http()) if wanted else None
    if listed is None:
        return adapter, None, False
    return adapter, listed.get(wanted), True


def board_status(url, http=None):
    """'Open', 'Closed' or 'Unknown' for a posting on an employer's hiring board; 'Unknown' for any other address."""
    try:
        config = identify(url)
        if not config or config.get("system") not in ADAPTERS:
            return "Unknown"
        adapter, job, readable = _board_job(url, http)
        if adapter is not None and not isinstance(adapter, _WholeBoard):
            return adapter.status(url, http or Http())
        if not readable:
            return "Unknown"
        return "Open" if job is not None else "Closed"
    except (requests.RequestException, ValueError, KeyError, TypeError):
        return "Unknown"


def board_workplace(url, http=None):
    """'Remote', 'Hybrid' or 'Onsite' as the employer's own board lists an open job, or None (not on a board that says,
    the job isn't listed, or the board couldn't be read)."""
    try:
        adapter, job, readable = _board_job(url, http)
        check = getattr(adapter, "workplace", None)
        return check(job) if readable and job is not None and check else None
    except (requests.RequestException, ValueError, KeyError, TypeError):
        return None
