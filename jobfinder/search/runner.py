"""The search itself: reads your titles and places, searches, checks every lead and saves the ones that
fit.
"""

import argparse
import sys


from jobfinder.records.board_health import HEALTH_FILE as BOARD_HEALTH_FILE
from jobfinder.records.search_debug import DebugRun
from jobfinder.records.search_skips import latest_decisions
from jobfinder.search import session
from jobfinder.search import shared
from jobfinder.search.geo import JOB_FINDER_VERSION
from jobfinder.search.refresh import update_existing_results
from jobfinder.search.searching import brave_enabled, brave_key
from jobfinder.search.shared import SEARCH_SKIPS_FILE, _board_health, _page_cache, _skip_decisions
from jobfinder.search.web_captures import import_captures
from jobfinder.sources.ats_lookup import clear_cache as clear_ats_cache
from jobfinder.sources.employer_site import clear_cache as clear_employer_cache
from jobfinder.search.run_employers import prepare_employers, search_employer_boards
from jobfinder.search.run_feeds import search_feeds
from jobfinder.search.run_setup import start_search
from jobfinder.search.run_sites import search_job_sites_quietly
from jobfinder.search.run_web import search_web_pages, web_queries
from jobfinder.search.run_wrapup import finish_search


def _run_search(job_title=None, state=None, cities_json=None, max_new=None):
    _page_cache.clear()
    _skip_decisions.clear()
    _skip_decisions.update(latest_decisions(SEARCH_SKIPS_FILE))
    if max_new is not None:
        shared.MAX_SEARCH_RESULTS = max_new
    run = start_search(job_title, state, cities_json)
    if run is None:
        return
    search_feeds(run)
    # Employer boards and job sites first; they need no search engine.
    prepare_employers(run)
    search_employer_boards(run)
    search_job_sites_quietly(run)

    if shared.stop_requested():
        print("Stop requested before the web search started.")
        run.database.close()
        return
    if not brave_enabled():
        print()
        print("Brave Search is switched off on the Tuning page, so the web search was skipped and no credits were used.", flush=True)
        if shared._debug_run is not None:
            shared._debug_run.stop_reason = "Brave Search switched off: web search skipped"
        run.database.close()
        return
    if not brave_key():
        print()
        print("No Brave Search API key, so the web search was skipped. Add one on the Tuning page.", flush=True)
        if shared._debug_run is not None:
            shared._debug_run.stop_reason = "No Brave Search API key: web search skipped"
        run.database.close()
        return
    print("Searching the web with Brave Search.")
    session.start()
    search_web_pages(run, web_queries(run))
    finish_search(run)


def main(job_title=None, state=None, cities_json=None, max_new=None):
    clear_employer_cache()
    clear_ats_cache()
    _board_health.clear()
    shared._debug_run = DebugRun(shared.SEARCH_DEBUG_FILE, "replacement" if max_new == 1 else "search", JOB_FINDER_VERSION)
    try:
        _run_search(job_title=job_title, state=state, cities_json=cities_json, max_new=max_new)
    except BaseException as error:
        shared._debug_run.stop_reason = f"stopped by {type(error).__name__}: {error}"
        raise
    finally:
        shared._debug_run.finish(shared._debug_run.stop_reason or "ended early (setup failed or a stop was requested)")
        shared._debug_run = None
        _board_health.write(BOARD_HEALTH_FILE, "replacement" if max_new == 1 else "search")


def parse_arguments():
    parser = argparse.ArgumentParser(description="Personal Job Finder")
    parser.add_argument("--update-existing", action="store_true")
    parser.add_argument("--update-ids", default=None)
    parser.add_argument("--import-captures", action="store_true",
                        help="Import jobs saved by the Web Job Scraper extension from web-job-scraper\\searches")
    parser.add_argument("--max-new", type=int, choices=range(1, 11), default=None)
    parser.add_argument("--job-title", default=None)
    parser.add_argument("--state", default=None)
    parser.add_argument("--cities-json", default=None)
    return parser.parse_args()


def command_line():
    """What job_finder.py does: a search, a refresh of saved results, or an import of captured jobs, by the arguments given."""
    for output in (sys.stdout, sys.stderr):
        if hasattr(output, "reconfigure"):
            output.reconfigure(encoding="utf-8", errors="backslashreplace")
    args = parse_arguments()

    if args.import_captures:
        import_captures()
    elif args.update_existing:
        update_existing_results([int(value) for value in args.update_ids.split(",") if value.isdecimal()] if args.update_ids is not None else None)
    else:
        main(job_title=args.job_title, state=args.state, cities_json=args.cities_json, max_new=args.max_new)
