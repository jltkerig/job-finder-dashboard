"""The web search's run: when it started, whether it must stop (stop request, time limit, Brave refusing), and why
it ended."""

import time

from jobfinder.search import shared
from jobfinder.search.shared import SEARCH_MAX_RUNTIME

start_time = None
engine_refused = False  # Brave refused (quota used up, bad key): no more queries this run
stop_announced = False


def start():
    global start_time, engine_refused
    start_time, engine_refused = time.time(), False


def web_search_ok():
    """False once a stop is requested or the search time limit is reached."""
    global stop_announced
    if shared.stop_requested():
        if not stop_announced:
            stop_announced = True
            print()
            print("Stop requested.")
        return False
    if start_time is not None and time.time() - start_time >= SEARCH_MAX_RUNTIME:
        print()
        print("The search reached its time limit.")
        return False
    return True


def search_stop_reason(saved, blocked_message):
    """Why the search ended, with what to change when that is something the user can act on."""
    if shared.stop_requested():
        return "Stopped by user"
    if saved >= shared.MAX_SEARCH_RESULTS:
        return f"{saved} of {shared.MAX_SEARCH_RESULTS} distinct jobs found"
    if blocked_message:
        return blocked_message
    if engine_refused:
        return ("Brave Search refused more queries (monthly quota used up or the API key is wrong). Check the key on "
                "the Tuning page.")
    if start_time is not None and time.time() - start_time >= SEARCH_MAX_RUNTIME:
        return (f"Search time limit reached ({round(SEARCH_MAX_RUNTIME / 60)} minutes). Raise the time limit on the "
                "Tuning page to search longer.")
    return ("Search sources exhausted: every query was checked. Try more job titles, more cities or a larger radius "
            "to find more.")
