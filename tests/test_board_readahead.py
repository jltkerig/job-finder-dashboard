import json
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import no_database  # noqa: F401  (cuts tests off from the real database)
from jobfinder.search import shared
from jobfinder.search.board_readahead import BoardReadAhead, ReadAheadStopped
from jobfinder.sources import employer_common
from test_search_flow import FakeEmployer, employer_opening, run_search


class Board:
    """A board whose reading can wait for others, fail, or make a request."""

    def __init__(self, name, openings=None, error=None, wait=None, asks=0):
        self.name, self.openings, self.error, self.wait, self.asks = name, openings or [], error, wait, asks

    def find_openings(self, titles, http):
        if self.wait:
            self.wait()
        for _ in range(self.asks):
            http.get("http://board.invalid/")  # the read-ahead's Http refuses once the search is over
        if self.error:
            raise self.error
        return self.openings


class ReadingAhead(unittest.TestCase):
    def test_boards_are_read_at_the_same_time(self):
        together = threading.Barrier(3, timeout=5)  # only passes when three boards are being read at once
        boards = [Board(f"b{i}", [i], wait=together.wait) for i in range(3)]
        ahead = BoardReadAhead(boards, ["Web Designer"], workers=3)
        self.assertEqual([ahead.openings(board) for board in boards], [[0], [1], [2]])
        ahead.close()

    def test_each_board_gives_its_own_openings_whatever_order_they_finish_in(self):
        slow_first = [Board("slow", ["a"], wait=lambda: time.sleep(0.2)), Board("quick", ["b"]), Board("quicker", ["c"])]
        ahead = BoardReadAhead(slow_first, ["Web Designer"], workers=3)
        self.assertEqual([ahead.openings(board) for board in slow_first], [["a"], ["b"], ["c"]])
        ahead.close()

    def test_the_next_board_starts_when_a_boards_turn_comes(self):
        started = []
        boards = [Board(f"b{i}", [i], wait=lambda i=i: started.append(i)) for i in range(5)]
        ahead = BoardReadAhead(boards, [], workers=2)
        time.sleep(0.2)
        self.assertEqual(sorted(started), [0, 1])  # only two are read until a turn is taken
        ahead.openings(boards[0])
        time.sleep(0.2)
        self.assertEqual(sorted(started), [0, 1, 2])
        ahead.close()

    def test_a_board_that_failed_raises_its_error_at_its_turn_and_the_others_are_fine(self):
        boards = [Board("ok", ["a"]), Board("down", error=ValueError("Workday answered HTTP 500")), Board("ok2", ["c"])]
        ahead = BoardReadAhead(boards, [], workers=3)
        self.assertEqual(ahead.openings(boards[0]), ["a"])
        with self.assertRaises(ValueError):
            ahead.openings(boards[1])
        self.assertEqual(ahead.openings(boards[2]), ["c"])
        ahead.close()

    def test_a_board_that_is_not_being_read_ahead_is_left_to_the_caller(self):
        ahead = BoardReadAhead([Board("a")], [], workers=1)
        self.assertIsNone(ahead.openings(Board("someone else")))
        ahead.close()

    def test_boards_still_being_read_stop_at_their_next_request_when_closed(self):
        entered, release = threading.Event(), threading.Event()

        def hold():
            entered.set()
            release.wait(5)

        board = Board("busy", asks=1, wait=hold)
        ahead = BoardReadAhead([board], [], workers=1)
        self.assertTrue(entered.wait(5))
        future = ahead._futures[id(board)]
        ahead.close()
        release.set()
        with self.assertRaises(ReadAheadStopped):
            future.result(timeout=5)  # no request went out: it stopped before the first one


class Pacing(unittest.TestCase):
    def test_calls_to_one_site_are_spaced_even_from_different_http_objects(self):
        sleeps = []
        with patch.object(employer_common.time, "sleep", sleeps.append), patch.dict(employer_common._host_next, clear=True):
            first, second = employer_common.Http(delay=0.4), employer_common.Http(delay=0.4)
            first._wait("https://api.example.test/a")
            second._wait("https://api.example.test/b")  # right after, on the same site: has to wait
            second._wait("https://other.example.test/c")  # another site: no wait
        self.assertEqual(len(sleeps), 1)
        self.assertGreater(sleeps[0], 0.3)

    def test_cookies_are_not_kept_between_calls(self):
        from http.cookiejar import Cookie
        from urllib.request import Request
        http = employer_common.Http()
        cookie = Cookie(0, "a", "b", None, False, "board.example.test", True, False, "/", True, False, None, False, None, None, {})
        http._session.cookies.set_cookie_if_ok(cookie, Request("https://board.example.test/"))
        self.assertEqual(len(http._session.cookies), 0)


class InTheSearch(unittest.TestCase):
    """The boards are checked and saved in the same order, with the same results, whether read ahead or not."""
    CITIES = json.dumps([{"city": "Baltimore, MD", "radius": 20}])

    def boards(self):
        def board(number, **kwargs):
            opening = dict(employer_opening(["Baltimore, MD, US"]), url=f"https://acme.example/jobs/{number}")
            made = FakeEmployer([opening], **kwargs)
            made.name = f"Acme {number}"
            made.config = dict(FakeEmployer.config, tenant=f"acme{number}")
            return made

        slow = board(1)
        real_read = slow.find_openings
        slow.find_openings = lambda titles, http: (time.sleep(0.25), real_read(titles, http))[1]
        return [slow, board(2), board(3, error=ValueError("Workday answered HTTP 500")), board(4)]

    def saved_urls(self, workers):
        captured = []
        near = lambda db, html, place, state, targets, **kw: (True, "Baltimore", 39.29, -76.61, 0.0)
        # The board list starts at a different board each hour; this pins it to the first.
        with patch.object(shared, "PREFETCH_WORKERS", workers), patch("jobfinder.search.runner.time.time", return_value=0.0):
            run_search({}, "https://example.com/none", "Web Designer", max_new=10, cities_json=self.CITIES,
                       city_targets=[{"city": "Baltimore, MD", "radius": 20}], distance=near,
                       extra={"load_employers": self.boards,
                              "save_company": lambda *a, **kw: captured.append(a[6]) or True})
        return captured

    def test_same_boards_same_order_with_or_without_reading_ahead(self):
        serial = self.saved_urls(1)
        parallel = self.saved_urls(4)
        self.assertEqual(serial, ["https://acme.example/jobs/1", "https://acme.example/jobs/2", "https://acme.example/jobs/4"])
        self.assertEqual(parallel, serial)


class ReplacementSearch(unittest.TestCase):
    """A replacement for one Top 10 pick tries the boards that matched last time first."""
    CITIES = json.dumps([{"city": "Baltimore, MD", "radius": 20}])

    def test_boards_that_matched_before_are_read_first(self):
        order = []

        class Quiet(FakeEmployer):
            def find_openings(self, titles, http):
                order.append(self.name)
                return []

        boards = []
        for number in (1, 2, 3):
            board = Quiet()
            board.name = f"Board {number}"
            board.config = dict(FakeEmployer.config, tenant=f"b{number}")
            boards.append(board)
        health = {"boards": [{"name": "Board 3", "matches": 4}, {"name": "Board 1", "matches": 0}]}
        with patch.object(shared, "PREFETCH_WORKERS", 1), patch("jobfinder.search.runner.time.time", return_value=0.0):
            run_search({}, "https://example.com/none", "Web Designer", max_new=1, cities_json=self.CITIES,
                       city_targets=[{"city": "Baltimore, MD", "radius": 20}], health=health,
                       extra={"load_employers": lambda: boards})
        self.assertEqual(order[0], "Board 3")


if __name__ == "__main__":
    unittest.main()
