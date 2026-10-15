"""The end of a search: the company-site check, the debug summary and the printed counts.
"""



from jobfinder.search import session
from jobfinder.search import shared
from jobfinder.search.relevance import fetch_text
from jobfinder.search import company_check
from jobfinder.search.searching import search_web
from jobfinder.search.shared import timed, timing_summary

def finish_search(run):
    """Say how the search went: the company-site check, the debug summary and the counts."""
    blocked_countries = duplicates = 0
    if run.companies_saved < shared.MAX_SEARCH_RESULTS:
        print()
        print(
            f"Search exhausted after saving {run.companies_saved}/"
            f"{shared.MAX_SEARCH_RESULTS} viable companies."
        )

    # While the search engine is still up: look for the employer's own posting of job-site and feed listings.
    if not shared.stop_requested() and session.web_search_ok():
        print()
        print("Checking company sites for job-site listings...", flush=True)
        try:
            with timed("Company-site check"):
                found_on_site = company_check.check_listings(run.database, search_web, fetch_text)
            print(f"Company-site check: found {found_on_site} on the employer's own site.", flush=True)
        except Exception as error:
            print(f"Company-site check stopped early: {error}", flush=True)

    run.database.close()

    print()
    print("=" * 60)
    print("SEARCH COMPLETE")
    print()
    stop_reason = session.search_stop_reason(run.companies_saved, run.blocked_message)
    print(f"Stop reason: {stop_reason}")
    if shared._debug_run is not None:
        shared._debug_run.stop_reason = stop_reason
        shared._debug_run.data["summary"] = {
            "results_checked": run.results_checked, "search_pages_checked": run.pages_checked,
            "websites_checked": run.websites_checked, "passed_validation": run.passed_count,
            "saved_new": run.companies_saved, "already_saved": run.existing_companies,
            "search_engines_blocking": bool(run.blocked_message),
            "seconds_spent": dict(timing_summary(), **{"Whole run": round(shared.run_seconds(), 1)})}

    print(f"Search results checked: {run.results_checked}")
    print(f"Search pages checked: {run.pages_checked}")
    for timing_name, timing_seconds in timing_summary().items():
        print(f"Time: {timing_name}: {timing_seconds:.0f}s")
    print(f"Time: Whole run: {shared.run_seconds():.0f}s")

    print(f"Aggregators skipped: " f"{run.blocked_sites}")

    print(f"Non-US domains skipped: " f"{blocked_countries}")

    print(f"Duplicates skipped: " f"{duplicates}")

    print(f"Existing companies updated: " f"{run.existing_companies}")

    print(f"Websites checked: " f"{run.websites_checked}")

    print(f"No career page: " f"{run.no_career_page}")


    print(f"Database saves: " f"{run.companies_saved}")
