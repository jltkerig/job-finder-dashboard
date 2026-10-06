"""The search itself: reads your titles and places, searches, checks every lead and saves the ones that
fit.
"""

import argparse
from datetime import datetime, timezone
import json
import re
import sys
import threading
import time
from urllib.parse import urljoin

from bs4 import BeautifulSoup
import requests

from jobfinder.profiles.onet_data import spelling_fix
from jobfinder.profiles.profile_tools import listing_skills
from jobfinder.records.board_health import HEALTH_FILE as BOARD_HEALTH_FILE
from jobfinder.records.capture_import import add_also_on, match_key, page_html as capture_page_html
from jobfinder.records.search_debug import DebugRun
from jobfinder.records.search_skips import cached_skip, latest_decisions, record_decision
from jobfinder.search import docker
from jobfinder.search import fetching
from jobfinder.search import geo
from jobfinder.search import judging
from jobfinder.search import relevance
from jobfinder.search import shared
from jobfinder.search import storage
from jobfinder.search.company_names import (
    OFFICIAL_BOARD_CREDIBILITY,
    company_board_posting,
    extract_company_name,
    tidy_company_name,
    verification_label,
)
from jobfinder.search.company_site import (
    ATS_DOMAINS,
    is_directory_or_marketplace_result,
    is_student_employment_overview,
)
from jobfinder.search.fetching import (
    get_domain,
    has_blocked_country_domain,
    is_blocked_domain,
    is_valid_url,
    prefetch_pages,
)
from jobfinder.search.geo import JOB_FINDER_VERSION, _ZIP_CODE
from jobfinder.search.refresh import (
    close_expired_listings_quietly,
    recheck_system_rejections_quietly,
    refresh_job_fit_quietly,
    update_existing_results,
)
from jobfinder.search.relevance import apply_link_closed, expand_job_titles, fetch_text
from jobfinder.search import company_check
from jobfinder.search.searching import brave_key, search_blocked_message, search_searxng
from jobfinder.search.shared import (
    BLOCKED_COMPANIES,
    BLOCKED_DOMAINS,
    EMPTY_QUERY_LIMIT,
    SEARCH_SKIPS_FILE,
    SITE_QUERY_PAGES,
    TIMEOUT,
    USA_ONLY,
    _board_health,
    _page_cache,
    _skip_decisions,
    debug_lead,
    debug_skip,
    is_internship,
    record_skip,
    related_family_titles,
    timed,
    timing_summary,
)
from jobfinder.search.usa_location import US_STATES, remote_state_restrictions, selected_state_codes
from jobfinder.search.web_captures import _row_details, import_captures, site_location_in_us
from jobfinder.sources.ats_discovery import identify as identify_board, identify_unreadable, pretty_name as board_name
from jobfinder.sources.ats_feeds import public_board_links
from jobfinder.sources.ats_lookup import clear_cache as clear_ats_cache
from jobfinder.sources.employer_jobs import (
    SOURCE_TYPE as EMPLOYER_SOURCE,
    Http as EmployerHttp,
    Employer,
    config_key,
    load_employers,
    record_board_result,
    save_discovered,
)
from jobfinder.sources.employer_site import clear_cache as clear_employer_cache, is_third_party
from jobfinder.sources.job_feeds import FEEDS
from jobfinder.sources.job_listings import (
    canonical_url,
    extract_jobs,
    is_article_page,
    is_pdf_url,
    job_links,
    pagination_links,
    matching_title as matching_job_title,
)
from jobfinder.sources.job_sites import JOB_SITES, SiteBlocked, search_places

web_search_started = False


def _run_search(job_title=None, state=None, cities_json=None, max_new=None):
    global web_search_started
    _page_cache.clear()
    _skip_decisions.clear()
    _skip_decisions.update(latest_decisions(SEARCH_SKIPS_FILE))
    if max_new is not None:
        shared.MAX_SEARCH_RESULTS = max_new
    print()
    print("================================")
    print("       PERSONAL JOB FINDER")
    print("================================")
    print()

    database = storage.connect_database()

    if database is None:
        return

    if not storage.ensure_database_schema(database):
        database.close()
        return
    refresh_job_fit_quietly(database)
    recheck_system_rejections_quietly(database)
    close_expired_listings_quietly(database)

    rejected_urls = storage.rejected_posting_urls(database)

    print()
    print("Connected to job_finder database.")

    print()

    if not job_title:
        job_title = input("Job title: ").strip()

    if not state:
        state = input("State: ").strip()

    job_titles = []
    seen_job_titles = set()
    for raw_title in job_title.split(","):
        cleaned_title = raw_title.strip()
        key = cleaned_title.lower()
        if cleaned_title and key not in seen_job_titles:
            seen_job_titles.add(key)
            job_titles.append(cleaned_title)

    if not job_titles:
        print("No valid job titles were provided.")
        database.close()
        return

    corrected_titles = []
    for index, typed_title in enumerate(job_titles):
        fixed = spelling_fix(typed_title)
        if fixed.casefold() != typed_title.casefold():
            print(f'Spelling corrected: "{typed_title}" -> "{fixed}"')
            corrected_titles.append((typed_title, fixed))
            job_titles[index] = fixed
    selected_titles = list(dict.fromkeys(job_titles))
    job_titles = expand_job_titles(selected_titles)
    if len(job_titles) > len(selected_titles):
        print("Related O*NET search titles: " + ", ".join(job_titles[len(selected_titles):]))
    # Web queries use the typed and O*NET titles; the related families below are only used to recognise openings.
    query_titles = list(job_titles)
    related_extras = [title for title in related_family_titles(selected_titles) if title.casefold() not in {t.casefold() for t in job_titles}]
    job_titles = job_titles + related_extras
    if related_extras:
        print("Also matching closely related titles: " + ", ".join(related_extras))

    try:
        cities = json.loads(cities_json or "[]")
        if not isinstance(cities, list):
            cities = []
    except json.JSONDecodeError:
        cities = []
    statewide_states = {US_STATES[str(item.get("city", "")).strip().casefold()]
                        for item in cities if isinstance(item, dict)
                        and str(item.get("city", "")).strip().casefold() in US_STATES}
    selected_states = selected_state_codes(state, cities, statewide_states)
    city_targets = geo.prepare_city_targets(database, state, cities)
    requested_cities = [item for item in cities if isinstance(item, dict)
                        and str(item.get("city", "")).strip()
                        and str(item.get("city", "")).strip().casefold() not in US_STATES]
    if requested_cities and not city_targets:
        print("ERROR: None of the selected cities could be geocoded. The search was stopped so the mileage filter would not be silently ignored.")
        database.close()
        raise SystemExit(2)
    if requested_cities and len(city_targets) < len(requested_cities):
        print(f"Warning: {len(requested_cities) - len(city_targets)} selected city/cities could not be geocoded and were skipped.")
    if statewide_states:
        print("Statewide locations: " + ", ".join(sorted(statewide_states)))
    if shared._debug_run is not None:
        shared._debug_run.set_inputs(
            job_title_typed=job_title, typed_titles=selected_titles,
            related_onet_titles=query_titles[len(selected_titles):], related_family_titles=related_extras,
            spelling_corrections=[{"typed": a, "corrected": b} for a, b in corrected_titles], state=state, cities_requested=cities,
            cities_geocoded=[{"city": target.get("city"), "radius": target.get("radius"),
                              "lat": target.get("lat"), "lon": target.get("lon")} for target in city_targets],
            statewide_states=sorted(statewide_states), max_new_results=shared.MAX_SEARCH_RESULTS,
            settings={"usa_only": USA_ONLY, "max_search_pages": shared.MAX_SEARCH_PAGES,
                      "request_delay_seconds": shared.REQUEST_DELAY, "website_timeout_seconds": TIMEOUT,
                      "searxng_timeout_minutes": shared.settings.get("searxng_timeout_minutes")},
            blocked_domain_count=len(BLOCKED_DOMAINS), blocked_company_count=len(BLOCKED_COMPANIES))

    print()
    print(f'Job titles: "{", ".join(job_titles)}"')
    print(f'State: "{state}"')
    print(f'Searching {len(job_titles)} job title(s).')

    fetched_pages = set()
    seen_openings = set()
    seen_result_urls = set()
    results_checked = 0
    candidate_number = 0
    pages_checked = 0


    websites_checked = 0
    companies_saved = 0
    passed_count = 0
    blocked_sites = 0
    blocked_countries = 0
    duplicates = 0
    existing_companies = 0
    no_career_page = 0

    # Public remote-job feeds. Each provider's own page stays the View link and the provider is named,
    # as their terms ask; employer URLs are not invented.
    feed_saved_total = 0
    feed_cap = max(1, shared.MAX_SEARCH_RESULTS // 3)
    for feed in FEEDS:
        if feed_saved_total >= feed_cap or companies_saved >= shared.MAX_SEARCH_RESULTS:
            break
        # Turned on by default. The blocked-domains list only affects web-search results, so these
        # aggregator sites can stay blocked there; switch one off here: "remote_feeds": {"Remotive": false}.
        if not shared.settings.get("remote_feeds", {}).get(feed.name, True):
            print(f"{feed.name} is turned off in settings.json.")
            _board_health.note(feed.name, "Remote feed", "off")
            continue
        print(f"Checking {feed.name} for matching remote listings...")
        try:
            feed_jobs = list(feed.matching(feed.fetch(), job_titles, USA_ONLY))
        except (requests.RequestException, ValueError, TypeError) as error:
            print(f"{feed.name} unavailable; continuing: {error}")
            _board_health.failed(feed.name, "Remote feed", error)
            continue
        print(f"{feed.name} matches: {len(feed_jobs)}")
        feed_saved = 0
        for job in feed_jobs:
            if (companies_saved >= shared.MAX_SEARCH_RESULTS or feed_saved_total >= feed_cap
                    or feed_saved >= max(1, shared.MAX_SEARCH_RESULTS // 3)):
                break
            candidate_number += 1
            results_checked += 1
            print(f"Checking result {candidate_number}: {job['title'][:75]} ({feed.name})")
            if not job["name"] or job["name"].casefold() in BLOCKED_COMPANIES:
                debug_skip("Blocked company", job["url"], job["title"])
                continue
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
            matched_title = next((wanted for wanted in job_titles if matching_job_title(job["title"], [wanted])), job_titles[0])
            inserted = storage.save_company(
                database, job["name"], job["title"], 6, feed.domain,
                job["url"], job["url"], "United States" if job["usa_score"] == 6 else None,
                job["location"][:100] or None, job["usa_score"], work_arrangement="Remote",
                skills=listing_skills(job["html"]), source_type=feed.name,
                listing_details={"location": job["location"], "posted": job.get("posted"),
                                 "evidence": [f"{feed.name} feed", "matching title"], "matched_title": matched_title,
                                 "verification": "From a remote-job feed; the company site was not checked"},
            )
            passed_count += 1
            print(f"Passed validation: {passed_count}")
            debug_lead(action="saved" if inserted else "already saved (updated)", source=f"{feed.name} feed",
                       title=job["title"], company=job["name"], url=job["url"], location=job["location"],
                       matched_title=matched_title, usa_score=job["usa_score"])
            if inserted:
                companies_saved += 1
                feed_saved += 1
                feed_saved_total += 1
                print(f"Saved viable company ({companies_saved}/{shared.MAX_SEARCH_RESULTS}).")
            else:
                existing_companies += 1
        _board_health.note(feed.name, "Remote feed", "ok" if feed_jobs else "no matches", len(feed_jobs), feed_saved)

    # Big employers' own career sites (watched_employers.json): Workday and Oracle searches. They go through
    # the same U.S., location and remote checks as web results.
    employer_saved_total = 0
    # Employer boards share up to half of a search's jobs, two per board, so several get a turn.
    employer_cap = max(1, shared.MAX_SEARCH_RESULTS // 2)
    employer_http = EmployerHttp()
    def search_employer(employer, per_employer_cap, total_cap):
        """Search one employer's board and save passing openings; returns how many were saved."""
        nonlocal candidate_number, results_checked, companies_saved, existing_companies, passed_count, employer_saved_total
        if employer_saved_total >= total_cap or companies_saved >= shared.MAX_SEARCH_RESULTS or shared.stop_requested():
            return 0
        print(f"Checking {employer.name} careers...")
        try:
            employer_openings = employer.find_openings(job_titles, employer_http)
        except (requests.RequestException, ValueError, KeyError, TypeError) as error:
            print(f"{employer.name} careers unavailable; continuing: {error}")
            _board_health.failed(employer.name, "Discovered board" if employer.discovered else "Employer board", error)
            return 0
        print(f"{employer.name} title matches: {len(employer_openings)}")
        if employer.discovered:
            record_board_result(employer.config, len(employer_openings))
        employer_saved = 0
        for opening in employer_openings:
            if (companies_saved >= shared.MAX_SEARCH_RESULTS or employer_saved_total >= total_cap
                    or employer_saved >= per_employer_cap):
                break
            job_url = canonical_url(opening["url"])
            candidate_number += 1
            results_checked += 1
            print(f"Checking result {candidate_number}: {opening['title'][:75]} ({employer.name})")
            if not job_url or job_url in seen_openings:
                continue
            if job_url in rejected_urls:
                record_skip("Previously rejected", job_url, opening["title"])
                continue
            seen_openings.add(job_url)
            if employer.name.casefold() in BLOCKED_COMPANIES:
                debug_skip("Blocked company", job_url, opening["title"])
                continue
            if USA_ONLY and opening["country"] and "united states" not in opening["country"].casefold():
                record_skip("Posting restricts applicants outside the US", job_url, opening["title"])
                continue
            # A posting can list several places; it passes if any one of them fits.
            outcome = None
            for place in opening["locations"]:
                outcome = judging.assess_opening(database, dict(opening, location=place), opening["html"], job_url, state,
                                         selected_states, statewide_states, city_targets)
                if not outcome["skip"]:
                    opening = dict(opening, location=place)
                    break
            if outcome["skip"]:
                record_skip(outcome["skip"], job_url, opening["title"])
                continue
            matched_title = next((wanted for wanted in job_titles + employer.extra_titles
                                  if matching_job_title(opening["title"], [wanted])), job_titles[0])
            details = {"schedule": opening["schedule"], "salary": opening["salary"], "posted": opening["posted"],
                       "evidence": opening["evidence"], "location": opening["location"], "matched_title": matched_title,
                       "remote_limited_to": sorted(outcome["remote_limited_to"]),
                       "verification": "Company's own careers site",
                       "source": employer.adapter.base if hasattr(employer.adapter, "base") else employer.domain}
            inserted = storage.save_company(database, employer.name, opening["title"], outcome["detail_score"], employer.domain,
                                    job_url, job_url, outcome["country"],
                                    str(outcome["state_name"] or "")[:100] or None, outcome["location"]["score"],
                                    city=outcome["city"], latitude=outcome["lat"], longitude=outcome["lon"],
                                    distance_miles=outcome["miles"], work_arrangement=outcome["arrangement"],
                                    skills=listing_skills(opening["html"]), source_type=EMPLOYER_SOURCE,
                                    listing_details=details)
            record_decision(SEARCH_SKIPS_FILE, _skip_decisions, "Passed", job_url, opening["title"])
            passed_count += 1
            print(f"Passed validation: {passed_count} · {opening['title']} — {', '.join(opening['evidence'])}", flush=True)
            debug_lead(action="saved" if inserted else "already saved (updated)", source=f"{employer.name} careers",
                       title=opening["title"], company=employer.name, url=job_url, location=opening["location"],
                       all_locations=opening["locations"], matched_title=matched_title, arrangement=outcome["arrangement"],
                       city=outcome["city"], distance_miles=outcome["miles"], remote_limited_to=sorted(outcome["remote_limited_to"]),
                       posted=opening["posted"])
            if inserted:
                companies_saved += 1
                employer_saved += 1
                employer_saved_total += 1
                print(f"Saved job {companies_saved}/{shared.MAX_SEARCH_RESULTS}: {job_url}")
            else:
                existing_companies += 1
        _board_health.note(employer.name, "Discovered board" if employer.discovered else "Employer board",
                           "ok" if employer_openings else "no matches", len(employer_openings), employer_saved)

        return employer_saved

    known_employers = load_employers()
    if known_employers:
        # Start at a different board each hour so a full quota does not always come from the first few in the list.
        turn = int(time.time() // 3600) % len(known_employers)
        known_employers = known_employers[turn:] + known_employers[:turn]
    known_boards = {config_key(employer.config) for employer in known_employers}
    unreadable_boards = set()
    boards_discovered = 0
    MAX_BOARDS_PER_RUN = 20

    def discover_board(page_url, company_hint=""):
        """A web result on a hiring platform (UltiPro, Greenhouse, ...): search that employer's whole board."""
        nonlocal boards_discovered
        if boards_discovered >= MAX_BOARDS_PER_RUN or shared.stop_requested():
            return
        config = identify_board(page_url)
        if not config:
            unreadable = identify_unreadable(page_url)
            if unreadable and config_key(unreadable) not in unreadable_boards:
                unreadable_boards.add(config_key(unreadable))
                print(f"Found a {unreadable['system'].title()} job board that Job Finder cannot read yet: {page_url[:110]}", flush=True)
            return
        key = config_key(config)
        if key in known_boards:
            return
        known_boards.add(key)
        boards_discovered += 1
        hint = re.sub(r"\s+", " ", str(company_hint or "")).strip()
        generic = not hint or len(hint) > 60 or hint.casefold() in {"job opportunities", "careers", "jobs", "job board"}
        name = board_name(config) if generic or config["system"] != "ultipro" else hint
        config = dict(config, name=name, discovered=True)
        try:
            employer = Employer(config)
        except KeyError:
            return
        print(f"Found {config['system'].title()} job board for {name}; searching its openings...", flush=True)
        save_discovered({k: v for k, v in config.items() if k != "discovered"})
        search_employer(employer, 3, shared.MAX_SEARCH_RESULTS)

    # Job sites that list many employers (the National Labor Exchange, usnlx.com): searched for the titles you
    # typed around each of your places. Their openings go through the same title, location and U.S. checks.
    site_saved_total = 0
    site_cap = max(1, shared.MAX_SEARCH_RESULTS // 3)
    same_job_index = None

    def saved_job_row(url):
        with database.cursor(dictionary=True) as cursor:
            cursor.execute("""SELECT id, name, career_job_title, work_arrangement, city, state, source_url, source_type,
                                     listing_details, is_rejected FROM companies WHERE source_url = %s LIMIT 1""", (url,))
            return cursor.fetchone()

    def index_saved_job(row):
        details = _row_details(row)
        place = details.get("location") or ", ".join(part for part in (row.get("city"), row.get("state")) if part)
        key = match_key(row.get("name"), row.get("career_job_title"), place, row.get("work_arrangement"))
        if key:
            same_job_index.setdefault(key, row)

    def same_saved_job(company, title, location, arrangement, url):
        """The job already saved from another place, when this opening is the same real job (same company and title
        in the same city, or both remote). None when it is new, or when the saved rows can't be read."""
        nonlocal same_job_index
        try:
            if same_job_index is None:
                same_job_index = {}
                with database.cursor(dictionary=True) as cursor:
                    cursor.execute("""SELECT id, name, career_job_title, work_arrangement, city, state, source_url,
                                             source_type, listing_details, is_rejected FROM companies""")
                    for row in cursor.fetchall():
                        index_saved_job(row)
        except Exception:
            same_job_index = {}
            return None
        key = match_key(company, title, location, arrangement)
        row = same_job_index.get(key) if key else None
        return row if row and canonical_url(row.get("source_url")) != canonical_url(url) else None

    def refresh_same_job(row, site, listing):
        """This search found a job we already have (saved from another site): mark it open and freshly checked, link
        this listing as another place it's posted, and fill in what the saved one lacks. Nothing else is changed."""
        details = _row_details(row)
        add_also_on(details, site.name, listing["url"])
        for field, value in (("posted", listing.get("posted")), ("location", listing.get("location")),
                             ("description", str(listing.get("description") or "")[:20000])):
            if value and not details.get(field):
                details[field] = value
        now = datetime.now(timezone.utc)
        with database.cursor() as cursor:
            cursor.execute("""UPDATE companies SET listing_details = %s, job_open_status = 'Open', last_checked = %s,
                              result_updated_at = %s WHERE id = %s""", (json.dumps(details), now, now, row["id"]))
        database.commit()
        row["listing_details"] = json.dumps(details)

    def check_site_listing(site, listing):
        """Filter and save one job-site opening; returns True when it was newly saved."""
        nonlocal candidate_number, results_checked, companies_saved, existing_companies, passed_count, site_saved_total
        job_url, title = listing["url"], listing["title"]
        candidate_number += 1
        results_checked += 1
        print(f"Checking result {candidate_number}: {title[:75]} ({site.name})")
        if not title or job_url in seen_openings:
            return False
        if job_url in rejected_urls:
            record_skip("Previously rejected", job_url, title)
            return False
        seen_openings.add(job_url)
        company = listing["company"] or "Unknown employer"
        if company.casefold() in BLOCKED_COMPANIES:
            debug_skip("Blocked company", job_url, title)
            return False
        if not matching_job_title(title, job_titles):
            record_skip("Title matches none of your job titles", job_url, title)
            return False
        if USA_ONLY and listing["country"] and "united states" not in listing["country"].casefold():
            record_skip("Posting restricts applicants outside the US", job_url, title)
            return False
        opening = {"title": title, "company": company, "description": listing["description"], "type": None,
                   "location": listing["location"], "locations": [listing["location"]], "remote_states": [],
                   "schedule": "", "evidence": [f"{site.name} listing"]}
        outcome = judging.assess_opening(database, opening, capture_page_html(listing), job_url, state, selected_states,
                                 statewide_states, city_targets)
        # The site's own location field ("Aberdeen, MD") is trusted as U.S. proof, as for captured LinkedIn jobs.
        if outcome["skip"] == "US eligibility unverified" and site_location_in_us(listing["location"]):
            outcome["skip"] = None
            outcome["country"] = "United States"
            outcome["location"]["score"] = max(outcome["location"]["score"], shared.USA_CREDIBILITY_THRESHOLD)
        if outcome["skip"]:
            record_skip(outcome["skip"], job_url, title)
            return False
        same = same_saved_job(company, title, listing["location"], outcome["arrangement"], job_url)
        if same:
            if same.get("is_rejected"):
                record_skip("Previously rejected", job_url, title)
                return False
            try:
                refresh_same_job(same, site, listing)
            except Exception as error:
                print(f"Could not update job #{same['id']}: {error}", flush=True)
                return False
            print(f"Already saved as job #{same['id']} ({same.get('source_type') or 'saved'}): updated it and linked this "
                  f"{site.name} listing — {title}", flush=True)
            debug_lead(action="already saved (updated)", source=site.name, title=title, company=company, url=job_url,
                       location=listing["location"], matched_title=None)
            existing_companies += 1
            return False
        matched_title = next((wanted for wanted in job_titles if matching_job_title(title, [wanted])), job_titles[0])
        details = {"posted": listing["posted"], "location": listing["location"], "matched_title": matched_title,
                   "evidence": [f"{site.name} listing", "title matches search"], "external_id": listing["guid"],
                   "remote_limited_to": sorted(outcome["remote_limited_to"]),
                   "verification": f"Listed on the {site.name}; the company site was not checked"}
        inserted = storage.save_company(database, company, title, outcome["detail_score"], site.domain, job_url, job_url,
                                outcome["country"], str(outcome["state_name"] or "")[:100] or None,
                                outcome["location"]["score"], city=outcome["city"], latitude=outcome["lat"],
                                longitude=outcome["lon"], distance_miles=outcome["miles"],
                                work_arrangement=outcome["arrangement"], skills=listing_skills(listing["description"]),
                                source_type=site.name, listing_details=details)
        record_decision(SEARCH_SKIPS_FILE, _skip_decisions, "Passed", job_url, title)
        passed_count += 1
        print(f"Passed validation: {passed_count} · {title} — {company} ({site.name})", flush=True)
        debug_lead(action="saved" if inserted else "already saved (updated)", source=site.name, title=title,
                   company=company, url=job_url, location=listing["location"], matched_title=matched_title)
        if inserted:
            companies_saved += 1
            site_saved_total += 1
            print(f"Saved job {companies_saved}/{shared.MAX_SEARCH_RESULTS}: {job_url}")
            try:
                if same_job_index is not None:
                    new_row = saved_job_row(job_url)
                    if new_row:
                        index_saved_job(new_row)  # a later site in this same search can then link to it
            except Exception:
                pass
        else:
            existing_companies += 1
        return inserted

    def search_job_sites():
        nonlocal site_saved_total
        places = search_places(state, cities, US_STATES)
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
            site_saved_total = 0  # each job site gets its own share of the search, so one can't use it all up
            print(f"Searching {site.name} for your titles near {', '.join(place for place, _ in places) or 'anywhere'}...")
            try:
                for title in selected_titles:
                    for place, radius in places:
                        if shared.stop_requested() or site_saved_total >= site_cap or companies_saved >= shared.MAX_SEARCH_RESULTS:
                            break
                        for listing in site.search(title, place, radius):
                            found += 1
                            if check_site_listing(site, listing):
                                saved += 1
                            if site_saved_total >= site_cap or companies_saved >= shared.MAX_SEARCH_RESULTS:
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

    def search_known_employers():
        try:
            with timed("Employer career boards"):
                for employer in known_employers:
                    search_employer(employer, 2, employer_cap)
                    if employer_saved_total >= employer_cap:
                        break
        except Exception as error:
            print(f"Employer career search stopped early: {error}", flush=True)
        try:
            with timed("Job sites"):
                search_job_sites()
        except Exception as error:
            print(f"Job site search stopped early: {error}", flush=True)

    # Employer boards are searched while Docker and SearXNG start (starting them takes a while), so neither waits
    # for the other. The web search begins only after both have finished.
    employer_thread = threading.Thread(target=search_known_employers, daemon=True)
    employer_thread.start()
    if brave_key() and not shared.stop_requested():
        # Brave Search needs no Docker; SearXNG starts only if Brave refuses partway through.
        print("Searching the web with Brave Search.")
        docker.brave_mode = True
        docker.searxng_start_time = time.time()
        docker_up = searxng_up = True
    else:
        docker_up = False if shared.stop_requested() else docker.start_docker_desktop()
        searxng_up = docker_up and docker.start_searxng()
    employer_thread.join()

    if shared.stop_requested():
        print("Stop requested before the web search started.")
        if docker_up:
            docker.stop_searxng()
            docker.stop_docker_desktop()
        database.close()
        return

    # The remote feed works without Docker. Search it first so a Docker failure
    # does not prevent independent API results from being saved.
    if not docker_up:
        database.close()
        return
    if not searxng_up:
        database.close()
        docker.stop_docker_desktop()
        return
    web_search_started = True

    # Only the titles you typed are searched on the company-board sites (every board found is then read in full).
    search_queries = [f'site:{site} "{title}"' for title in selected_titles for site in shared.JOB_BOARD_SITES]
    city_names = [item.get("city", "").strip() for item in cities if isinstance(item, dict)
                  and item.get("city") and item.get("city", "").strip().casefold() not in US_STATES
                  and not _ZIP_CODE.fullmatch(item.get("city", "").strip())]
    for title in query_titles:
        title_queries = []
        for city_name in city_names:
            title_queries.extend([
                f'"{title}" "{city_name}" "{state}" jobs',
                f'"{title}" "{city_name}" careers',
            ])
        title_queries.extend([
            f"{title} {state}".strip(),
            f'"{title}" "{state}" careers',
            f'"{title}" "{state}" jobs',
            f'{title} careers {state}',
            f'{title} hiring {state}',
            f'"{title}" freelance project {state}',
            f'"{title}" contract gig {state}',
            # Local employers whose openings the big job sites often miss.
            f'"{title}" state government jobs {state}',
            f'"{title}" university jobs {state}',
            f'"{title}" hospital health system careers {state}',
        ])
        for candidate_query in title_queries:
            if candidate_query not in search_queries:
                search_queries.append(candidate_query)


    empty_query_streak = 0
    blocked_message = None

    for search_query in search_queries:
        if companies_saved >= shared.MAX_SEARCH_RESULTS or blocked_message or not docker.check_searxng_timer():
            break

        print()
        print(f'Search variation: "{search_query}"')
        if shared._debug_run is not None:
            shared._debug_run.query(search_query)

        empty_or_repeating_pages = 0
        dry_pages = 0

        query_pages = SITE_QUERY_PAGES if search_query.startswith("site:") else shared.MAX_SEARCH_PAGES
        for page in range(1, query_pages + 1):
            if companies_saved >= shared.MAX_SEARCH_RESULTS or not docker.check_searxng_timer():
                break

            results = search_searxng(search_query, page)
            if shared._debug_run is not None:
                shared._debug_run.query_page(len(results or []))
            if page == 1:
                # Many queries in a row with no results at all means the engines are refusing us.
                empty_query_streak = 0 if results else empty_query_streak + 1
                if empty_query_streak >= EMPTY_QUERY_LIMIT:
                    blocked_message = search_blocked_message(empty_query_streak)
                    print()
                    print(blocked_message, flush=True)
                    break

            if not results:
                empty_or_repeating_pages += 1
                if empty_or_repeating_pages >= 2:
                    break
                continue

            new_results = [
                result
                for result in results
                if result.get("url")
                and result.get("url") not in seen_result_urls
            ]

            if not new_results:
                empty_or_repeating_pages += 1
                if empty_or_repeating_pages >= 2:
                    break
                continue

            empty_or_repeating_pages = 0
            mostly_seen = len(results) >= 5 and len(new_results) / len(results) < 0.2
            passed_before = passed_count
            seen_result_urls.update(result.get("url") for result in new_results)
            pages_checked += 1
            # PDFs are ignored outright: not fetched, counted, or recorded as skips.
            web_results = [result for result in new_results if not is_pdf_url(result.get("url"))]
            pdfs_ignored = len(new_results) - len(web_results)
            results_checked += len(web_results)
            print(f"Checking search page {page} ({len(web_results)} new results"
                  + (f", {pdfs_ignored} PDFs ignored" if pdfs_ignored else "") + ")...")

            # Download this page's results in parallel; the checks below then read them from the cache.
            prefetch_pages([url for url in (canonical_url(result.get("url") or "") for result in web_results)
                            if url and not is_blocked_domain(get_domain(url)) and not has_blocked_country_domain(get_domain(url))
                            and not cached_skip(_skip_decisions, url)][:24])

            for result in web_results:
                if companies_saved >= shared.MAX_SEARCH_RESULTS or not docker.check_searxng_timer():
                    break
                candidate_number += 1
                title = str(result.get("title") or "")
                url = canonical_url(result.get("url") or "")
                if not url or not is_valid_url(url):
                    continue
                domain = get_domain(url)
                if is_blocked_domain(domain) or has_blocked_country_domain(domain):
                    blocked_sites += 1
                    record_skip("Blocked domain", url, title)
                    continue
                print(f"Checking result {candidate_number}: {title[:75]}", flush=True)
                recent_skip = cached_skip(_skip_decisions, url)
                if recent_skip:
                    print(f"Skipped (recent check: {recent_skip['reason']}): {title[:70]} {url[:120]}", flush=True)
                    continue
                discover_board(url, title)
                websites_checked += 1
                landing = fetching.safe_request(url)
                if landing is None:
                    record_skip("Page unavailable", url, title)
                    continue
                landing_soup = BeautifulSoup(landing.text, "html.parser")
                if is_directory_or_marketplace_result(domain, title, landing.text):
                    record_skip("Directory or marketplace page", url, title)
                    continue
                if is_article_page(landing_soup) or is_student_employment_overview(url, landing.text):
                    record_skip("Article or student employment guide", url, title)
                    continue
                company_name = extract_company_name(landing_soup, title, domain)
                if company_name.strip().casefold() in BLOCKED_COMPANIES:
                    debug_skip("Blocked company", url, title)
                    continue
                # Follow the site's career navigation, then individual job links.
                # Each URL is fetched at most once per search and failures stay local.
                pending = [url]
                for link in landing_soup.find_all("a", href=True):
                    marker = f"{link.get_text(' ', strip=True)} {link['href']}".casefold()
                    if any(term in marker for term in ("career", "job openings", "open positions", "join our team")):
                        candidate = canonical_url(urljoin(url, link["href"]))
                        if candidate and (get_domain(candidate) == domain or any(get_domain(candidate).endswith(ats) for ats in ATS_DOMAINS)):
                            pending.append(candidate)
                pending.extend(job_links(url, landing.text))
                for board_url in pending[:10]:
                    pending.extend(public_board_links(board_url, job_titles))
                checked = set()
                while pending and len(checked) < 18 and companies_saved < shared.MAX_SEARCH_RESULTS and docker.check_searxng_timer():
                    page_url = pending.pop(0)
                    if page_url != url:
                        discover_board(page_url, company_name)
                    if page_url in checked or page_url in fetched_pages or is_pdf_url(page_url):
                        continue
                    checked.add(page_url)
                    fetched_pages.add(page_url)
                    page = landing if page_url == url else fetching.safe_request(page_url)
                    if page is None:
                        record_skip("Job page unavailable", page_url, title)
                        continue
                    openings = extract_jobs(page.url, page.text, job_titles)
                    if not openings and len(checked) < 8:
                        pending.extend(job_links(page.url, page.text, limit=24))
                        pending.extend(pagination_links(page.url, page.text))
                        pending.extend(public_board_links(page.url, job_titles))
                        prefetch = list(dict.fromkeys(candidate for candidate in pending
                                                       if candidate not in checked and candidate not in fetched_pages))[:4]
                        prefetch_pages(prefetch, workers=4)
                    elif not openings:
                        record_skip("No matching individual opening", page.url, title)
                    for opening in openings:
                        job_url = canonical_url(opening["url"])
                        if not job_url or job_url in seen_openings:
                            continue
                        if job_url in rejected_urls:
                            debug_skip("Previously rejected", job_url, opening["title"])
                            continue
                        seen_openings.add(job_url)
                        if is_blocked_domain(get_domain(job_url)):
                            debug_skip("Blocked domain", job_url, opening["title"])
                            continue
                        name = tidy_company_name(opening["company"]) or company_name
                        if name.casefold() in BLOCKED_COMPANIES:
                            debug_skip("Blocked company", job_url, opening["title"])
                            continue
                        details = {key: opening.get(key) for key in ("schedule", "salary", "posted", "evidence", "location")}
                        details["source"] = url
                        details["matched_title"] = next((wanted for wanted in job_titles if matching_job_title(opening["title"], [wanted])), job_titles[0])
                        outcome = judging.assess_opening(database, opening, page.text, job_url, state, selected_states,
                                                 statewide_states, city_targets)
                        if outcome["skip"]:
                            record_skip(outcome["skip"], job_url, opening["title"])
                            continue
                        arrangement, location, detail_score = outcome["arrangement"], outcome["location"], outcome["detail_score"]
                        country, state_name, remote_limited_to = outcome["country"], outcome["state_name"], outcome["remote_limited_to"]
                        details["remote_limited_to"] = sorted(remote_limited_to)
                        city, lat, lon, miles = outcome["city"], outcome["lat"], outcome["lon"], outcome["miles"]
                        # A repost on a job board can outlive the real opening; check where Apply leads.
                        closed = apply_link_closed(job_url, page.text) if is_third_party(job_url, name) else None
                        if closed:
                            record_skip("Job is closed (expired)", job_url, opening["title"])
                            debug_skip(f"closed: {closed['reason']}", closed["apply_link"], opening["title"])
                            continue
                        # A listing found on a job board should link to the employer's own careers page.
                        employer_notes = []
                        employer = None
                        if is_third_party(job_url, name):
                            employer = relevance.find_employer_site(
                                name, opening["title"], job_url, page.text, job_titles,
                                location_hint=opening["location"] or f"{city or ''} {state_name or ''}".strip(),
                                notes=employer_notes)
                        row_domain, row_career_url = get_domain(job_url), job_url
                        if employer:
                            row_domain = employer["domain"]
                            # View goes to the job itself; the employer's careers page is shown separately.
                            row_career_url = employer["posting_url"] or job_url
                            details["employer_site"] = {"domain": employer["domain"],
                                                        "careers_url": employer["careers_url"],
                                                        "method": employer["method"],
                                                        "posting_found": bool(employer["posting_url"])}
                            details["original_source"] = job_url
                            details["evidence"] = list(details.get("evidence") or []) + employer["evidence"]
                            print(f"Employer site: {employer['domain']} ({employer['method']}) -> {row_career_url}", flush=True)
                        elif employer_notes:
                            print("Employer site not found: " + "; ".join(employer_notes), flush=True)
                        # The job board is only a copy: use the company's own hiring-board posting when it has one.
                        board_posting = company_board_posting(name, opening["title"]) if is_third_party(job_url, name) else None
                        if board_posting:
                            row_career_url = board_posting["url"]
                            detail_score = max(detail_score, OFFICIAL_BOARD_CREDIBILITY)
                            details["ats_posting"] = {"system": board_posting["system"], "url": board_posting["url"]}
                            details.setdefault("original_source", job_url)
                            details["evidence"] = list(details.get("evidence") or []) + [
                                f"posting found on the company's own {board_posting['system'].title()} board"]
                            print(f"Company's own posting: {board_posting['url']}", flush=True)
                        details["verification"] = verification_label(details, None, name, job_url)
                        inserted = storage.save_company(database, name, opening["title"], detail_score,
                                                row_domain, row_career_url, job_url, country,
                                                str(state_name or "")[:100] or None, location["score"], city=city,
                                                latitude=lat, longitude=lon, distance_miles=miles,
                                                work_arrangement=arrangement, skills=listing_skills(page.text),
                                                listing_details=details)
                        record_decision(SEARCH_SKIPS_FILE, _skip_decisions, "Passed", job_url, opening["title"])
                        passed_count += 1
                        print(f"Passed validation: {passed_count} · {opening['title']} — {', '.join(details.get('evidence') or ['individual opening'])}", flush=True)
                        debug_lead(action="saved" if inserted else "already saved (updated)", title=opening["title"],
                                   company=name, url=job_url, view_link=row_career_url, domain=row_domain,
                                   matched_title=details["matched_title"], evidence=details.get("evidence"),
                                   found_by_query=search_query, search_result={"title": title, "url": url},
                                   listing_page=page.url, location=opening["location"],
                                   location_check={"score": location["score"], "state": location["state"],
                                                   "country": country, "evidence": location.get("evidence")},
                                   arrangement=arrangement, city=city, distance_miles=miles,
                                   remote_limited_to=sorted(remote_limited_to),
                                   employer_site=details.get("employer_site"), employer_notes=employer_notes,
                                   career_credibility=detail_score, salary=details.get("salary"),
                                   posted=details.get("posted"))
                        if inserted:
                            companies_saved += 1
                            print(f"Saved job {companies_saved}/{shared.MAX_SEARCH_RESULTS}: {job_url}")
                        else:
                            existing_companies += 1
                if not checked:
                    no_career_page += 1

            dry_pages = 0 if passed_count > passed_before else dry_pages + 1
            if mostly_seen:
                print("Most results on this page were already seen; moving to the next query.", flush=True)
                break
            if dry_pages >= 3:
                print("Three search pages in a row gave no matching jobs; moving to the next query.", flush=True)
                break

    if companies_saved < shared.MAX_SEARCH_RESULTS:
        print()
        print(
            f"Search exhausted after saving {companies_saved}/"
            f"{shared.MAX_SEARCH_RESULTS} viable companies."
        )

    # While the search engine is still up: look for the employer's own posting of job-site and feed listings.
    if not shared.stop_requested() and docker.check_searxng_timer():
        print()
        print("Checking company sites for job-site listings...", flush=True)
        try:
            found_on_site = company_check.check_listings(database, search_searxng, fetch_text)
            print(f"Company-site check: found {found_on_site} on the employer's own site.", flush=True)
        except Exception as error:
            print(f"Company-site check stopped early: {error}", flush=True)

    database.close()

    print()
    print("=" * 60)
    print("SEARCH COMPLETE")
    print()
    stop_reason = docker.search_stop_reason(companies_saved, blocked_message)
    print(f"Stop reason: {stop_reason}")
    if shared._debug_run is not None:
        shared._debug_run.stop_reason = stop_reason
        shared._debug_run.data["summary"] = {
            "results_checked": results_checked, "search_pages_checked": pages_checked,
            "websites_checked": websites_checked, "passed_validation": passed_count,
            "saved_new": companies_saved, "already_saved": existing_companies,
            "search_engines_blocking": bool(blocked_message), "seconds_spent": timing_summary()}

    print(f"Search results checked: {results_checked}")
    print(f"Search pages checked: {pages_checked}")
    for timing_name, timing_seconds in timing_summary().items():
        print(f"Time: {timing_name}: {timing_seconds:.0f}s")

    print(f"Aggregators skipped: " f"{blocked_sites}")

    print(f"Non-US domains skipped: " f"{blocked_countries}")

    print(f"Duplicates skipped: " f"{duplicates}")

    print(f"Existing companies updated: " f"{existing_companies}")

    print(f"Websites checked: " f"{websites_checked}")

    print(f"No career page: " f"{no_career_page}")


    print(f"Database saves: " f"{companies_saved}")


def main(job_title=None, state=None, cities_json=None, max_new=None):
    global web_search_started
    web_search_started = False
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
        # Also runs after a crash or stop request, so SearXNG and Docker are not left running.
        if web_search_started:
            docker.stop_searxng()
            docker.stop_docker_desktop()
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
