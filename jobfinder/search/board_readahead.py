"""Reading employer career boards a few at a time.

A search goes through the watched and discovered boards one by one, and most of the wait is the boards answering.
Boards on different sites do not depend on each other, so the next few are read while the current one is checked.
The checks and saves still happen one board at a time and in the same order as before; only the reading overlaps.
"""

from concurrent.futures import ThreadPoolExecutor
import threading

from jobfinder.search import shared
from jobfinder.sources.employer_jobs import Http


class ReadAheadStopped(Exception):
    """A board that was still being read when the search no longer needed it."""


class _ReadAheadHttp(Http):
    """An Http for one board's reads that gives up (before its next request) once the search is over or stopped."""

    def __init__(self, cancelled):
        super().__init__()
        self._cancelled = cancelled

    def _wait(self, url):
        if self._cancelled.is_set() or shared.stop_requested():
            raise ReadAheadStopped()
        super()._wait(url)


class BoardReadAhead:
    """Starts reading `workers` boards at once, and the next one each time a board's turn comes (see `openings`)."""

    def __init__(self, employers, titles, workers):
        self._titles = list(titles)
        self._cancelled = threading.Event()
        self._pool = ThreadPoolExecutor(max_workers=max(1, workers), thread_name_prefix="board-readahead")
        self._pending = iter(employers)
        self._futures = {}  # id(employer) -> the future reading it
        for _ in range(max(1, workers)):
            self._start_next()

    def _start_next(self):
        employer = next(self._pending, None)
        if employer is not None:
            self._futures[id(employer)] = self._pool.submit(self._read, employer)

    def _read(self, employer):
        http = _ReadAheadHttp(self._cancelled)
        try:
            return employer.find_openings(self._titles, http)
        finally:
            http.close()

    def openings(self, employer):
        """The employer's openings, or None when it is not being read ahead (the caller then reads it itself).
        A board that failed raises the same error reading it directly would have raised."""
        future = self._futures.pop(id(employer), None)
        if future is None:
            return None
        self._start_next()
        return future.result()

    def close(self):
        """The search no longer needs the boards still being read: they stop at their next request."""
        self._cancelled.set()
        self._pool.shutdown(wait=False, cancel_futures=True)
