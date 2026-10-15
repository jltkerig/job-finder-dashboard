"""Public remote-job feeds (Remote OK, Remotive, We Work Remotely).
"""


import requests

from jobfinder.profiles.profile_tools import listing_skills
from jobfinder.search import shared
from jobfinder.search import storage
from jobfinder.search.shared import (
    BLOCKED_COMPANIES,
    USA_ONLY,
    _board_health,
    debug_lead,
    debug_skip,
    is_internship,
    record_skip,
    timed,
)
from jobfinder.search.usa_location import remote_state_restrictions
from jobfinder.sources.job_feeds import FEEDS
from jobfinder.sources.job_listings import canonical_url, matching_title as matching_job_title

from jobfinder.search.run_state import title_label


def search_feeds(run):
    """Public remote-job feeds, searched before anything else."""
    database, job_titles, rejected_urls = run.database, run.job_titles, run.rejected_urls
    selected_states = run.selected_states
    feed_saved_total = 0
    feed_cap = max(1, shared.MAX_SEARCH_RESULTS // 3)
    for feed in FEEDS:
        if feed_saved_total >= feed_cap or run.companies_saved >= shared.MAX_SEARCH_RESULTS:
            break
        # Turned on by default. The blocked-domains list only affects web-search results, so these
        # aggregator sites can stay blocked there; switch one off here: "remote_feeds": {"Remotive": false}.
        if not shared.settings.get("remote_feeds", {}).get(feed.name, True):
            print(f"{feed.name} is turned off in settings.json.")
            _board_health.note(feed.name, "Remote feed", "off")
            continue
        print(f"Checking {feed.name} for matching remote listings...")
        try:
            with timed("Remote job feeds"):
                feed_jobs = list(feed.matching(feed.fetch(), job_titles, USA_ONLY))
        except (requests.RequestException, ValueError, TypeError) as error:
            print(f"{feed.name} unavailable; continuing: {error}")
            _board_health.failed(feed.name, "Remote feed", error)
            continue
        print(f"{feed.name} matches: {len(feed_jobs)}")
        feed_saved = 0
        same_in_feed = set()  # We Work Remotely posts one job under several addresses (…-governance, …-governance-1)
        for job in feed_jobs:
            if (run.companies_saved >= shared.MAX_SEARCH_RESULTS or feed_saved_total >= feed_cap
                    or feed_saved >= max(1, shared.MAX_SEARCH_RESULTS // 3)):
                break
            run.candidate_number += 1
            run.results_checked += 1
            print(f"Checking result {run.candidate_number}: {job['title'][:75]} ({feed.name})")
            if not job["name"] or job["name"].casefold() in BLOCKED_COMPANIES:
                debug_skip("Blocked company", job["url"], job["title"])
                continue
            same_key = (job["name"].casefold(), job["title"].casefold())
            if same_key in same_in_feed:
                debug_skip("Same job listed again in the feed", job["url"], job["title"])
                continue
            same_in_feed.add(same_key)
            if canonical_url(job["url"]) in rejected_urls:
                record_skip("Previously rejected", job["url"], job["title"])
                continue
            if is_internship(job["title"]):
                record_skip("Internship", job["url"], job["title"])
                continue
            limited_to = remote_state_restrictions(job["text"])
            if limited_to and selected_states and not (limited_to & selected_states):
                record_skip("Remote job limited to residents of " + ", ".join(sorted(limited_to)), job["url"], job["title"])
                continue
            matched_title = title_label(next((wanted for wanted in job_titles if matching_job_title(job["title"], [wanted])), job_titles[0]))
            inserted = storage.save_company(
                database, job["name"], job["title"], 6, feed.domain,
                job["url"], job["url"], "United States" if job["usa_score"] == 6 else None,
                job["location"][:100] or None, job["usa_score"], work_arrangement="Remote",
                skills=listing_skills(job["html"]), source_type=feed.name,
                listing_details={"location": job["location"], "posted": job.get("posted"),
                                 "evidence": [f"{feed.name} feed", "matching title"], "matched_title": matched_title,
                                 "verification": "From a remote-job feed; the company site was not checked"},
            )
            run.passed_count += 1
            print(f"Passed validation: {run.passed_count}")
            debug_lead(action="saved" if inserted else "already saved (updated)", source=f"{feed.name} feed",
                       title=job["title"], company=job["name"], url=job["url"], location=job["location"],
                       matched_title=matched_title, usa_score=job["usa_score"])
            if inserted:
                run.companies_saved += 1
                feed_saved += 1
                feed_saved_total += 1
                print(f"Saved viable company ({run.companies_saved}/{shared.MAX_SEARCH_RESULTS}).")
            else:
                run.existing_companies += 1
        _board_health.note(feed.name, "Remote feed", "ok" if feed_jobs else "no matches", len(feed_jobs), feed_saved)
