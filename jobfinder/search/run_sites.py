"""Job sites that list many employers (the National Labor Exchange, usnlx.com): searched for the titles you typed around each of your places.
"""

from datetime import datetime, timezone
import json

import requests

from jobfinder.profiles.profile_tools import listing_skills
from jobfinder.records.capture_import import add_also_on, match_key, page_html as capture_page_html
from jobfinder.records.search_skips import record_decision
from jobfinder.search import judging
from jobfinder.search import shared
from jobfinder.search import storage
from jobfinder.search.shared import (
    BLOCKED_COMPANIES,
    SEARCH_SKIPS_FILE,
    USA_ONLY,
    _board_health,
    _skip_decisions,
    debug_lead,
    debug_skip,
    record_skip,
    timed,
)
from jobfinder.search.usa_location import US_STATES
from jobfinder.search.web_captures import _row_details, site_location_in_us
from jobfinder.sources.job_listings import canonical_url, matching_title as matching_job_title
from jobfinder.sources.job_sites import JOB_SITES, SiteBlocked, search_places

from jobfinder.search.run_state import title_label


def saved_job_row(run, url):
    with run.database.cursor(dictionary=True) as cursor:
        cursor.execute("""SELECT id, name, career_job_title, work_arrangement, city, state, source_url, source_type,
                                 listing_details, is_rejected FROM companies WHERE source_url = %s LIMIT 1""", (url,))
        return cursor.fetchone()


def index_saved_job(run, row):
    details = _row_details(row)
    place = details.get("location") or ", ".join(part for part in (row.get("city"), row.get("state")) if part)
    key = match_key(row.get("name"), row.get("career_job_title"), place, row.get("work_arrangement"))
    if key:
        run.same_job_index.setdefault(key, row)


def same_saved_job(run, company, title, location, arrangement, url):
    """The job already saved from another place, when this opening is the same real job (same company and title
    in the same city, or both remote). None when it is new, or when the saved rows can't be read."""
    try:
        if run.same_job_index is None:
            run.same_job_index = {}
            with run.database.cursor(dictionary=True) as cursor:
                cursor.execute("""SELECT id, name, career_job_title, work_arrangement, city, state, source_url,
                                         source_type, listing_details, is_rejected FROM companies""")
                for row in cursor.fetchall():
                    index_saved_job(run, row)
    except Exception:
        run.same_job_index = {}
        return None
    key = match_key(company, title, location, arrangement)
    row = run.same_job_index.get(key) if key else None
    return row if row and canonical_url(row.get("source_url")) != canonical_url(url) else None


def refresh_same_job(run, row, site, listing):
    """This search found a job we already have (saved from another site): mark it open and freshly checked, link
    this listing as another place it's posted, and fill in what the saved one lacks. Nothing else is changed."""
    details = _row_details(row)
    add_also_on(details, site.name, listing["url"])
    for field, value in (("posted", listing.get("posted")), ("location", listing.get("location")),
                         ("description", str(listing.get("description") or "")[:20000])):
        if value and not details.get(field):
            details[field] = value
    now = datetime.now(timezone.utc)
    with run.database.cursor() as cursor:
        cursor.execute("""UPDATE companies SET listing_details = %s, job_open_status = 'Open', last_checked = %s,
                          result_updated_at = %s WHERE id = %s""", (json.dumps(details), now, now, row["id"]))
    run.database.commit()
    row["listing_details"] = json.dumps(details)


def check_site_listing(run, site, listing):
    """Filter and save one job-site opening; returns True when it was newly saved."""
    job_url, title = listing["url"], listing["title"]
    run.candidate_number += 1
    run.results_checked += 1
    print(f"Checking result {run.candidate_number}: {title[:75]} ({site.name})")
    if not title or job_url in run.seen_openings:
        return False
    if job_url in run.rejected_urls:
        record_skip("Previously rejected", job_url, title)
        return False
    run.seen_openings.add(job_url)
    company = listing["company"] or "Unknown employer"
    if company.casefold() in BLOCKED_COMPANIES:
        debug_skip("Blocked company", job_url, title)
        return False
    if not matching_job_title(title, run.job_titles):
        record_skip("Title matches none of your job titles", job_url, title)
        return False
    if USA_ONLY and listing["country"] and "united states" not in listing["country"].casefold():
        record_skip("Posting restricts applicants outside the US", job_url, title)
        return False
    opening = {"title": title, "company": company, "description": listing["description"], "type": None,
               "location": listing["location"], "locations": [listing["location"]], "remote_states": [],
               "schedule": "", "evidence": [f"{site.name} listing"]}
    outcome = judging.assess_opening(run.database, opening, capture_page_html(listing), job_url, run.state, run.selected_states,
                             run.statewide_states, run.city_targets)
    # The site's own location field ("Aberdeen, MD") is trusted as U.S. proof, as for captured LinkedIn jobs.
    if outcome["skip"] == "US eligibility unverified" and site_location_in_us(listing["location"]):
        outcome["skip"] = None
        outcome["country"] = "United States"
        outcome["location"]["score"] = max(outcome["location"]["score"], shared.USA_CREDIBILITY_THRESHOLD)
    if outcome["skip"]:
        record_skip(outcome["skip"], job_url, title)
        return False
    same = same_saved_job(run, company, title, listing["location"], outcome["arrangement"], job_url)
    if same:
        if same.get("is_rejected"):
            record_skip("Previously rejected", job_url, title)
            return False
        try:
            refresh_same_job(run, same, site, listing)
        except Exception as error:
            print(f"Could not update job #{same['id']}: {error}", flush=True)
            return False
        print(f"Already saved as job #{same['id']} ({same.get('source_type') or 'saved'}): updated it and linked this "
              f"{site.name} listing — {title}", flush=True)
        debug_lead(action="already saved (updated)", source=site.name, title=title, company=company, url=job_url,
                   location=listing["location"], matched_title=None)
        run.existing_companies += 1
        return False
    matched_title = title_label(next((wanted for wanted in run.job_titles if matching_job_title(title, [wanted])), run.job_titles[0]))
    details = {"posted": listing["posted"], "location": listing["location"], "matched_title": matched_title,
               "evidence": [f"{site.name} listing", "title matches search"], "external_id": listing["guid"],
               "remote_limited_to": sorted(outcome["remote_limited_to"]),
               "verification": f"Listed on the {site.name}; the company site was not checked",
               "description": (listing.get("description") or "")[:20000]}
    inserted = storage.save_company(run.database, company, title, outcome["detail_score"], site.domain, job_url, job_url,
                            outcome["country"], str(outcome["state_name"] or "")[:100] or None,
                            outcome["location"]["score"], city=outcome["city"], latitude=outcome["lat"],
                            longitude=outcome["lon"], distance_miles=outcome["miles"],
                            work_arrangement=outcome["arrangement"], skills=listing_skills(listing["description"]),
                            source_type=site.name, listing_details=details)
    record_decision(SEARCH_SKIPS_FILE, _skip_decisions, "Passed", job_url, title)
    run.passed_count += 1
    print(f"Passed validation: {run.passed_count} · {title} — {company} ({site.name})", flush=True)
    debug_lead(action="saved" if inserted else "already saved (updated)", source=site.name, title=title,
               company=company, url=job_url, location=listing["location"], matched_title=matched_title)
    if inserted:
        run.companies_saved += 1
        run.site_saved_total += 1
        print(f"Saved job {run.companies_saved}/{shared.MAX_SEARCH_RESULTS}: {job_url}")
        try:
            if run.same_job_index is not None:
                new_row = saved_job_row(run, job_url)
                if new_row:
                    index_saved_job(run, new_row)  # a later site in this same search can then link to it
        except Exception:
            pass
    else:
        run.existing_companies += 1
    return inserted


def search_job_sites(run):
    site_cap = max(1, shared.MAX_SEARCH_RESULTS // 3)
    places = search_places(run.state, run.cities, US_STATES)
    for site_class in JOB_SITES:
        if not shared.settings.get("job_sites", {}).get(site_class.name, True):
            print(f"{site_class.name} is turned off in settings.json.")
            _board_health.note(site_class.name, "Job site", "off")
            continue
        site = site_class()
        if not getattr(site, "configured", True):
            print(f"{site.name} needs setup, so it was skipped: {site.setup_hint}")
            _board_health.note(site.name, "Job site", "off", detail=site.setup_hint)
            continue
        found = saved = 0
        run.site_saved_total = 0  # each job site gets its own share of the search, so one can't use it all up
        print(f"Searching {site.name} for your titles near {', '.join(place for place, _ in places) or 'anywhere'}...")
        try:
            for title in run.selected_titles or [""]:  # "": any job, searched by place only
                for place, radius in places:
                    if shared.stop_requested() or run.site_saved_total >= site_cap or run.companies_saved >= shared.MAX_SEARCH_RESULTS:
                        break
                    for listing in site.search(title, place, radius):
                        found += 1
                        if check_site_listing(run, site, listing):
                            saved += 1
                        if run.site_saved_total >= site_cap or run.companies_saved >= shared.MAX_SEARCH_RESULTS:
                            break
        except SiteBlocked as error:
            print(f"{site.name} asked Job Finder to slow down; leaving it alone for the rest of this search ({error}).")
            _board_health.failed(site.name, "Job site", error)
            continue
        except (requests.RequestException, ValueError, KeyError, TypeError) as error:
            print(f"{site.name} unavailable; continuing: {error}")
            _board_health.failed(site.name, "Job site", error)
            continue
        print(f"{site.name}: {found} openings read in {site.requests} requests, {saved} new saved.")
        _board_health.note(site.name, "Job site", "ok" if found else "no matches", found, saved)


def search_job_sites_quietly(run):
    try:
        with timed("Job sites"):
            search_job_sites(run)
    except Exception as error:
        print(f"Job site search stopped early: {error}", flush=True)
