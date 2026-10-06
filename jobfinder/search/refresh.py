"""Refreshing results already saved: checking they are still open, re-checking rejected ones and
updating Job Fit.
"""

from datetime import date, datetime, timezone
import json
from pathlib import Path

from mysql.connector import Error
import requests

from jobfinder import paths
from jobfinder.profiles.profile_tools import listing_skills, refresh_listing_skills
from jobfinder.profiles.travel import miles_between
from jobfinder.records.capture_import import CAPTURE_SOURCES
from jobfinder.records.job_retention import CLOSED_KEEP_DAYS, tidy_closed_jobs
from jobfinder.records.search_debug import DebugRun
from jobfinder.search import fetching
from jobfinder.search import geo
from jobfinder.search import relevance
from jobfinder.search import shared
from jobfinder.search import storage
from jobfinder.search import web_captures
from jobfinder.search.company_names import (
    OFFICIAL_BOARD_CREDIBILITY,
    company_board_posting,
    on_company_site,
    on_official_board,
    name_from_url,
    tidy_company_name,
    verification_label,
)
from jobfinder.search.company_site import inspect_company_site, score_career_page
from jobfinder.search.fetching import get_domain, is_valid_url, prefetch_for_update
from jobfinder.search.geo import JOB_FINDER_VERSION, distance_to_city_targets
from jobfinder.search.relevance import (
    fetch_text,
    apply_link_closed,
    expand_job_titles,
    is_internship_row,
    is_irrelevant_lead,
    is_wrong_location_lead,
    load_city_targets,
    load_selected_states,
    load_user_titles,
    reject_irrelevant_row,
    stale_remote_limit,
)
from jobfinder.search.shared import CAREER_CREDIBILITY_THRESHOLD, related_family_titles
from jobfinder.search.usa_location import (
    US_STATES,
    analyze_usa_location,
    detect_work_arrangement,
    merge_location_data,
    selected_state_codes,
)
from jobfinder.sources.employer_jobs import (
    SOURCE_TYPE as EMPLOYER_SOURCE,
    Http as EmployerHttp,
    employer_for_url,
    load_employers,
)
from jobfinder.sources.employer_site import clear_cache as clear_employer_cache, is_third_party
from jobfinder.sources.job_feeds import FEEDS, FEED_NAMES
from jobfinder.sources.job_listings import canonical_url, extract_jobs, looks_like_not_a_job, matching_title as matching_job_title
from jobfinder.sources.job_sites import JOB_SITE_NAMES, job_site_for
from jobfinder.search import company_check
from jobfinder.search.searching import brave_key, search_web

SEARCH_SCOPE_FILE = paths.SEARCH_SCOPE_FILE
RESTORABLE_REASONS = ("wrong_role", "wrong_location")


def current_search_scope(database):
    """What the profile asks for now (titles, cities with radius, state) in a comparable form, or None if unreadable."""
    titles, cities, state, problem = web_captures.load_profile_filters(database)
    if problem:
        return None
    return {
        "titles": sorted({title.strip().casefold() for title in titles if title.strip()}),
        "cities": sorted(f"{str(item.get('city') or '').strip().casefold()}|{item.get('radius') or ''}" for item in cities),
        "state": str(state or "").strip().casefold(),
    }


def location_matches_scope(row, city_targets, statewide, selected_states):
    """Would this saved listing's place pass the location filter with the profile as it is now?"""
    code = US_STATES.get(str(row.get("state") or "").strip().casefold())
    if code and code in statewide:
        return True
    try:
        point = (float(row["latitude"]), float(row["longitude"]))
    except (KeyError, TypeError, ValueError):
        point = None
    if city_targets:
        return bool(point) and any(miles_between(point, (target["lat"], target["lon"])) <= target["radius"]
                                   for target in city_targets)
    return bool(code) and code in selected_states


def restore_rejected_row(database, company_id):
    """Put a listing the system rejected back in the results, as the Restore button does."""
    with database.cursor() as cursor:
        cursor.execute(
            """UPDATE companies
               SET is_rejected = 0, is_kept = COALESCE(pre_reject_kept, 0),
                   application_status = COALESCE(pre_reject_status, 'None'),
                   rejected_at = NULL, rejection_reason = NULL, rejected_by = NULL,
                   pre_reject_kept = NULL, pre_reject_status = NULL
               WHERE id = %s AND is_rejected = 1 AND rejected_by = 'system'""", (company_id,))
    database.commit()


def recheck_system_rejections(database, scope_file=None):
    """When the profile's titles or places changed since the last check, look again at the listings Job Finder itself
    rejected for the wrong title or the wrong place, and restore those that now fit. A listing the person rejected is
    never touched (rejected_by is 'user', or empty for older rows). Returns how many were restored."""
    scope_file = Path(scope_file or SEARCH_SCOPE_FILE)
    scope = current_search_scope(database)
    if scope is None:
        return 0
    try:
        earlier = json.loads(scope_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        earlier = None
    try:
        scope_file.write_text(json.dumps(scope), encoding="utf-8")
    except OSError:
        pass
    if earlier is None or earlier == scope:
        return 0
    titles, cities, state, _ = web_captures.load_profile_filters(database)
    wanted = expand_job_titles(titles) if titles else []
    wanted += [title for title in related_family_titles(titles) if title.casefold() not in {w.casefold() for w in wanted}]
    statewide = {US_STATES[str(item["city"]).strip().casefold()] for item in cities
                 if str(item["city"]).strip().casefold() in US_STATES}
    selected_states = selected_state_codes(state, cities, statewide)
    city_targets = geo.prepare_city_targets(database, state, cities)
    with database.cursor(dictionary=True) as cursor:
        placeholders = ", ".join(["%s"] * len(RESTORABLE_REASONS))
        cursor.execute(
            f"""SELECT id, career_job_title, state, latitude, longitude, rejection_reason FROM companies
                WHERE is_rejected = 1 AND rejected_by = 'system' AND rejection_reason IN ({placeholders})""",
            RESTORABLE_REASONS)
        rows = cursor.fetchall()
    restored = 0
    for row in rows:
        if row["rejection_reason"] == "wrong_role":
            fits = bool(wanted) and matching_job_title(row.get("career_job_title") or "", wanted)
        else:
            fits = location_matches_scope(row, city_targets, statewide, selected_states)
        if fits:
            restore_rejected_row(database, row["id"])
            restored += 1
    return restored


def recheck_system_rejections_quietly(database):
    """Runs at the start of a search or Refresh; never stops it."""
    try:
        restored = recheck_system_rejections(database)
    except Exception:
        return
    if restored:
        print(f"Search changed: {restored} listing(s) rejected earlier now fit and were restored.", flush=True)


def close_expired_listings(database, today=None):
    """Mark Closed the open listings whose own closing date (kept when the page was captured) has passed.
    Returns how many were closed."""
    today = today or date.today()
    with database.cursor() as cursor:
        cursor.execute("""SELECT id, listing_details FROM companies
                          WHERE job_open_status <> 'Closed' AND listing_details LIKE %s""", ('%"closes"%',))
        rows = cursor.fetchall()
    expired = []
    for company_id, raw in rows:
        try:
            closes = date.fromisoformat(str((json.loads(raw or "{}") or {}).get("closes") or "")[:10])
        except (TypeError, ValueError, AttributeError):
            continue
        if closes < today:
            expired.append(company_id)
    if expired:
        now = datetime.now(timezone.utc)
        with database.cursor() as cursor:
            for company_id in expired:
                cursor.execute("""UPDATE companies SET job_open_status = 'Closed', last_checked = %s, result_updated_at = %s
                                  WHERE id = %s""", (now, now, company_id))
        database.commit()
    return len(expired)


def close_expired_listings_quietly(database):
    try:
        closed = close_expired_listings(database)
    except Exception:
        return
    if closed:
        print(f"Closed: {closed} listing(s) passed their closing date.", flush=True)


def refresh_job_fit_quietly(database):
    """Listings saved earlier pick up skills the current skills list recognizes, so Job Fit uses them. Never stops a run."""
    try:
        changed = refresh_listing_skills(database)
    except Exception:
        return
    if changed:
        print(f"Job Fit: {changed} saved listing(s) now list newly recognized skills.", flush=True)


def update_existing_results(company_ids=None):
    """Recheck existing rows without running the web search."""
    shared.update_existing_mode = True
    print()
    print("================================")
    print("     UPDATE EXISTING RESULTS")
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
    cursor = None
    try:
        cursor = database.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT
                id,
                name,
                career_job_title,
                domain,
                career_url,
                source_url,
                country,
                state,
                usa_credibility,
                career_credibility,
                work_arrangement,
                listing_skills,
                listing_details,
                source_type,
                is_kept,
                job_open_status
            FROM companies
            WHERE is_rejected = 0
            """ + (" AND id IN (" + ",".join(["%s"] * len(company_ids)) + ")" if company_ids is not None else "") + " ORDER BY date_found ASC",
            tuple(company_ids) if company_ids is not None else (),
        )
        companies = cursor.fetchall()
    except Error as error:
        print("Could not read existing companies for update.")
        print(error)
        if cursor is not None:
            cursor.close()
        database.close()
        return
    finally:
        if cursor is not None:
            cursor.close()

    print(f"Found {len(companies)} existing results to verify.")

    wanted_titles = expand_job_titles(load_user_titles(database))
    selected_now = load_selected_states(database)
    city_targets_now, search_state_text = load_city_targets(database)
    clear_employer_cache()
    shared._debug_run = DebugRun(shared.SEARCH_DEBUG_FILE, "update", JOB_FINDER_VERSION)
    shared._debug_run.set_inputs(rows_checked=len(companies), update_ids=company_ids,
                          titles_used_for_relevance=wanted_titles)

    updated = 0
    failed = 0
    rejected_count = 0
    employers = load_employers() if any(row.get("source_type") == EMPLOYER_SOURCE for row in companies) else []
    employer_http = EmployerHttp()
    feed_listings = {}  # provider name -> {listing URL: listing}, or None when its feed could not be read
    for feed in FEEDS:
        if any(row.get("source_type") == feed.name for row in companies):
            try:
                feed_listings[feed.name] = feed.index(feed.fetch())
            except (requests.RequestException, ValueError, TypeError) as error:
                feed_listings[feed.name] = None
                print(f"{feed.name} refresh unavailable: {error}")

    for index, company in enumerate(companies, start=1):
        if (index - 1) % 20 == 0:
            prefetch_for_update([url for row in companies[index - 1:index + 19]
                                 if row.get("source_type") not in FEED_NAMES and row.get("source_type") != EMPLOYER_SOURCE
                                 and row.get("source_type") not in CAPTURE_SOURCES
                                 and row.get("source_type") not in JOB_SITE_NAMES
                                 for url in (row.get("source_url"), row.get("career_url"))])
        company_id = company["id"]
        if company.get("source_type") in CAPTURE_SOURCES:
            # LinkedIn and the other job sites answer with a sign-in page, which would read as a closed job, and
            # direct visits could get noticed. The Web Job Scraper extension reports these jobs' status instead.
            print()
            print(f"Updating {index}/{len(companies)}: {company.get('name') or company.get('domain')}")
            print(f"Not rechecked: {company.get('source_type')} jobs are kept current by the Web Job Scraper extension.")
            continue
        career_url = company.get("career_url")
        source_url = company.get("source_url")
        search_title = company.get("career_job_title") or company.get("name") or ""
        try:
            details = json.loads(company.get("listing_details") or "{}")
        except (TypeError, ValueError):
            details = {}
        work_arrangement = detect_work_arrangement(search_title) or company.get("work_arrangement")
        skills = None

        print()
        print("=" * 60)
        print(f"Updating {index}/{len(companies)}: {company.get('name') or company.get('domain')}")
        remote_limit = None
        if not (is_irrelevant_lead(company, details, wanted_titles) or is_wrong_location_lead(company, details)):
            remote_limit = stale_remote_limit(company, details, selected_now, employer_http)
        internship = is_internship_row(company, details)
        not_a_job = not company.get("is_kept") and looks_like_not_a_job(company.get("career_job_title"))
        reject_reason = ("not_a_job" if not_a_job
                         else "wrong_role" if internship or is_irrelevant_lead(company, details, wanted_titles)
                         else "wrong_location" if is_wrong_location_lead(company, details) or remote_limit else None)
        if reject_reason:
            try:
                rejected = reject_irrelevant_row(database, company_id, reject_reason)
            except Error as error:
                database.rollback()
                failed += 1
                print(f"Could not reject this row: {error}")
                continue
            if rejected:
                updated += 1
                rejected_count += 1
                why = ("is an internship" if internship
                       else "matches none of your job titles" if reject_reason == "wrong_role"
                       else f"is remote but only open to residents of {', '.join(sorted(remote_limit))}" if remote_limit
                       else f"is located outside the United States ({details.get('location')})")
                print(f"Rejected: '{search_title}' {why}. It is in Rejected Listings for review.")
                shared._debug_run.lead(action="rejected", id=company_id, title=search_title,
                                company=company.get("name"), url=source_url or career_url,
                                reason=reject_reason, detail=why)
                continue
        tidy_name = tidy_company_name(company.get("name"), company.get("career_job_title")) or name_from_url(company.get("career_url")) or "Unknown employer"
        if tidy_name and tidy_name != company.get("name"):
            try:
                with database.cursor() as rename_cursor:
                    rename_cursor.execute("UPDATE companies SET name = %s WHERE id = %s", (tidy_name, company_id))
                database.commit()
                print(f"Company name tidied: {company.get('name')} -> {tidy_name}")
                company["name"] = tidy_name
            except Error as error:
                database.rollback()
                print(f"Could not tidy the company name: {error}")
        site_class = job_site_for(company.get("source_type"))
        if site_class:
            # A job-site opening is open while the site still lists it (its page is an app that shows nothing to
            # a plain download, so the page itself can't be checked).
            status = site_class().status(source_url)
            try:
                with database.cursor() as status_cursor:
                    status_cursor.execute(
                        """UPDATE companies SET job_open_status = %s, last_checked = %s,
                           result_updated_at = CASE WHEN job_open_status <> %s THEN %s ELSE result_updated_at END
                           WHERE id = %s""",
                        (status, datetime.now(timezone.utc), status, datetime.now(timezone.utc), company_id))
                database.commit()
                updated += 1
                print(f"{site_class.name} says: {status}.")
                print("Existing row updated in place.")
            except Error as error:
                database.rollback()
                failed += 1
                print(f"Could not update {company.get('name')}: {error}")
            continue
        if company.get("source_type") == EMPLOYER_SOURCE:
            employer = employer_for_url(source_url or "", employers)
            try:
                status = employer.status(source_url, employer_http) if employer else "Unknown"
            except (requests.RequestException, ValueError, KeyError, TypeError) as error:
                status = "Unknown"
                print(f"Could not check {company.get('name')}: {error}")
            try:
                with database.cursor() as status_cursor:
                    status_cursor.execute(
                        """UPDATE companies SET job_open_status = %s, last_checked = %s,
                           result_updated_at = CASE WHEN job_open_status <> %s THEN %s ELSE result_updated_at END
                           WHERE id = %s""",
                        (status, datetime.now(timezone.utc), status, datetime.now(timezone.utc), company_id))
                database.commit()
                updated += 1
                print(f"Employer careers site says: {status}.")
            except Error as error:
                database.rollback()
                failed += 1
                print(f"Could not update {company.get('name')}: {error}")
            continue
        if company.get("source_type") in FEED_NAMES:
            feed_name = company.get("source_type")
            remote_feed = feed_listings.get(feed_name)
            item = (remote_feed or {}).get(source_url)
            if item:
                try:
                    new_skills = listing_skills(item.get("description") or "")
                    feed_location = str(item.get("location") or "")[:100] or None
                    location_changed = company.get("state") != feed_location
                    status_changed = company.get("job_open_status") != "Open"
                    with database.cursor() as feed_cursor:
                        feed_cursor.execute(
                            """UPDATE companies SET listing_skills = %s, state = %s,
                               job_open_status = 'Open', last_checked = %s,
                               result_updated_at = CASE WHEN %s THEN %s ELSE result_updated_at END
                               WHERE id = %s""",
                            (json.dumps(new_skills), feed_location, datetime.now(timezone.utc),
                             location_changed or status_changed, datetime.now(timezone.utc), company_id),
                        )
                    database.commit()
                    updated += 1
                    print(f"{feed_name} listing still appears in the recent feed.")
                except Error as error:
                    database.rollback()
                    failed += 1
                    print(f"Could not refresh {feed_name} listing: {error}")
            elif remote_feed is not None:
                try:
                    with database.cursor() as feed_cursor:
                        feed_cursor.execute(
                            """UPDATE companies SET job_open_status = 'Unknown', last_checked = %s,
                               result_updated_at = CASE WHEN job_open_status <> 'Unknown' THEN %s ELSE result_updated_at END
                               WHERE id = %s""",
                            (datetime.now(timezone.utc), datetime.now(timezone.utc), company_id),
                        )
                    database.commit()
                    updated += 1
                    print("Not in the recent feed; listing status is Unknown until verified at the source.")
                except Error as error:
                    database.rollback()
                    failed += 1
                    print(f"Could not update {feed_name} listing status: {error}")
            else:
                failed += 1
                print(f"{feed_name} feed unavailable; status left unchanged.")
            continue

        career_credibility = 0
        job_open_status = company.get("job_open_status") or "Open"
        location_data = {
            "country": None,
            "state": None,
            "score": 0,
            "evidence": [],
        }
        employer = None
        employer_notes = []
        apply_closed = None
        ats_found = False
        repaired_domain = None
        landing_html = ""

        if source_url and is_valid_url(source_url):
            direct_posting = (company.get("source_type") == EMPLOYER_SOURCE or bool(
                {"JobPosting data", "job detail page", "direct listing link"} & set(details.get("evidence") or [])))
            # A row with missing location data still gets the deep read, so refresh keeps filling it in.
            direct_posting = direct_posting and bool(company.get("country")) and bool(company.get("usa_credibility"))
            source_data = inspect_company_site(
                homepage=source_url,
                search_title=search_title,
                domain=company.get("domain") or get_domain(source_url),
                deep=not direct_posting,
            )
            location_data = merge_location_data(location_data, source_data["location"])
            work_arrangement = work_arrangement or detect_work_arrangement("", source_data.get("landing_html", ""))
            landing_html = source_data.get("landing_html", "")
            # A job-board repost can outlive the real opening; check where its Apply button leads.
            if is_third_party(source_url, company.get("name") or ""):
                apply_closed = apply_link_closed(source_url, source_data.get("landing_html", ""))
            # A listing found through a job board should link to the employer's own careers page.
            if not details.get("employer_site") and is_third_party(source_url, company.get("name") or ""):
                employer = relevance.find_employer_site(
                    company.get("name") or "", search_title, source_url, source_data.get("landing_html", ""),
                    wanted_titles or [search_title], location_hint=str(details.get("location") or ""),
                    allow_search=False, notes=employer_notes)
                if employer:
                    career_url = employer["posting_url"] or source_url
                    details["employer_site"] = {"domain": employer["domain"], "careers_url": employer["careers_url"],
                                                "method": employer["method"],
                                                "posting_found": bool(employer["posting_url"])}
                    details["original_source"] = source_url
                    details["evidence"] = list(details.get("evidence") or []) + employer["evidence"]
                    print(f"Employer site found: {employer['domain']} ({employer['method']}) -> {career_url}")
                else:
                    print("Employer site not found: " + "; ".join(employer_notes or ["no candidates"]))
        if (not details.get("ats_posting") and source_url and is_third_party(source_url, company.get("name") or "")
                and not company.get("is_kept")):
            board_posting = company_board_posting(company.get("name") or "", search_title)
            if board_posting:
                career_url = board_posting["url"]
                details["ats_posting"] = {"system": board_posting["system"], "url": board_posting["url"]}
                details.setdefault("original_source", source_url)
                details["evidence"] = list(details.get("evidence") or []) + [
                    f"posting found on the company's own {board_posting['system'].title()} board"]
                ats_found = True
                print(f"Company's own posting found: {career_url}")
        saved_site = details.get("employer_site") or {}
        listing_host = get_domain(source_url or "")
        if (saved_site.get("domain") and listing_host != saved_site["domain"]
                and listing_host.endswith("." + saved_site["domain"])):
            # The listing is on a subdomain of the company's website (corcoran.gwu.edu under gwu.edu): show that site.
            saved_site["domain"] = repaired_domain = listing_host
            saved_site["posting_found"] = True
            print(f"Company website is {listing_host} (the listing's own site)")
        employer_row = bool(details.get("employer_site"))
        # Rows saved earlier linked View to the employer's careers page; point it back at the job itself.
        saved_careers = (details.get("employer_site") or {}).get("careers_url")
        repaired_view = ats_found
        if saved_careers and source_url and career_url == saved_careers and source_url != saved_careers:
            career_url = source_url
            repaired_view = True
            print(f"View link now goes to the job posting itself: {source_url}")

        if career_url and is_valid_url(career_url):
            shared._last_failure_status = None
            response = fetching.safe_request(career_url)
            if response is not None:
                # The employer's careers page is not the job description, so keep the skills already saved.
                if not employer_row or canonical_url(career_url) == canonical_url(source_url or ""):
                    skills = listing_skills(response.text)
                current_postings = extract_jobs(career_url, response.text, [search_title])
                matching_posting = next((opening for opening in current_postings
                                         if canonical_url(opening["url"]) == canonical_url(career_url)), None)
                # A page's structured URL can differ from the saved one; accept its only
                # posting as long as the page itself was not redirected elsewhere.
                if (matching_posting is None and len(current_postings) == 1
                        and canonical_url(response.url) == canonical_url(career_url)):
                    matching_posting = current_postings[0]
                work_arrangement = (matching_posting or {}).get("type") or work_arrangement or detect_work_arrangement("", response.text)
                career_data = score_career_page(career_url, response.text)
                career_credibility = career_data["score"]
                active_posting = bool(details and matching_posting)
                if active_posting:
                    career_credibility = max(CAREER_CREDIBILITY_THRESHOLD, career_credibility)
                    if on_official_board(career_url):
                        career_credibility = max(OFFICIAL_BOARD_CREDIBILITY, career_credibility)
                if details.get("ats_posting"):
                    career_credibility = max(OFFICIAL_BOARD_CREDIBILITY, career_credibility)
                # An employer page that does not list the job says nothing about the original listing.
                if details and not active_posting and not employer_row:
                    job_open_status = "Unknown"
                elif active_posting or career_credibility >= CAREER_CREDIBILITY_THRESHOLD:
                    job_open_status = "Open"
                elif career_credibility <= 2 and not employer_row:
                    job_open_status = "Closed"
                career_location = analyze_usa_location(
                    response.text,
                    extra_text=search_title,
                    source_label=f"career page {career_url}",
                    page_url=response.url,
                )
                location_data = merge_location_data(location_data, career_location)

                print(f"Career credibility: {career_credibility}")
                for reason in career_data.get("evidence", []):
                    print(f"  Evidence: {reason}")
            elif shared._last_failure_status in (404, 410):
                job_open_status = "Closed"
                print(f"Career page returned HTTP {shared._last_failure_status}; marking job Closed.")
            else:
                job_open_status = "Unknown"
                print("Career page could not be loaded right now; marking job Unknown.")
        else:
            job_open_status = "Closed"
            print("No usable career URL; marking job Closed.")

        if apply_closed:
            job_open_status = "Closed"
            print(f"Job is closed: {apply_closed['reason']} ({apply_closed['url'][:90]})")
            shared._debug_run.note(f"row {company_id} ({company.get('name')}) marked Closed: {apply_closed['reason']} "
                            f"[{apply_closed['apply_link']}]")

        if location_data["score"] == 0 and not location_data["evidence"]:
            location_data["country"] = company.get("country")
            location_data["state"] = company.get("state")
            location_data["score"] = company.get("usa_credibility") or 0

        # A row saved before city radii (or with its address at the bottom of the page) gets its distance filled in.
        found_place = None
        if (city_targets_now and landing_html and company.get("distance_miles") is None and not company.get("city")
                and work_arrangement != "Remote"):
            _, place_city, place_lat, place_lon, place_miles = distance_to_city_targets(
                database, landing_html, str(details.get("location") or search_title), search_state_text, city_targets_now,
                allow_footer=on_company_site(source_url, company.get("name"), details))
            if place_lat is not None:
                found_place = (place_city, place_lat, place_lon, place_miles)
                print(f"Distance filled in: {place_city}, {place_miles} miles from the nearest selected city")
        new_label = verification_label(details, company.get("source_type"), company.get("name"), source_url)
        details_dirty = bool(employer) or ats_found or bool(repaired_domain) or details.get("verification") != new_label
        details["verification"] = new_label
        update_cursor = None
        try:
            changed = any((
                company.get("work_arrangement") != work_arrangement,
                company.get("career_credibility") != career_credibility,
                (company.get("job_open_status") or "Open") != job_open_status,
                company.get("country") != location_data.get("country"),
                company.get("state") != location_data.get("state"),
                (company.get("usa_credibility") or 0) != location_data.get("score", 0),
                employer is not None,
                repaired_view,
                found_place is not None,
                bool(apply_closed),
            ))
            checked_at = datetime.now(timezone.utc)
            update_cursor = database.cursor()
            update_cursor.execute(
                """
                UPDATE companies
                SET
                    career_credibility = %s,
                    job_open_status = %s,
                    source_type = COALESCE(NULLIF(source_type, ''), 'SearXNG'),
                    country = %s,
                    state = %s,
                    usa_credibility = %s,
                    work_arrangement = %s,
                    listing_skills = COALESCE(%s, listing_skills),
                    domain = COALESCE(%s, domain),
                    career_url = COALESCE(%s, career_url),
                    listing_details = COALESCE(%s, listing_details),
                    city = COALESCE(%s, city),
                    latitude = COALESCE(%s, latitude),
                    longitude = COALESCE(%s, longitude),
                    distance_miles = COALESCE(%s, distance_miles),
                    last_checked = %s,
                    result_updated_at = CASE WHEN %s THEN %s ELSE result_updated_at END
                WHERE id = %s
                """,
                (
                    career_credibility,
                    job_open_status,
                    location_data.get("country"),
                    location_data.get("state"),
                    location_data.get("score", 0),
                    work_arrangement,
                    json.dumps(skills) if skills is not None else None,
                    employer["domain"] if employer else repaired_domain,
                    career_url if (employer or repaired_view) else None,
                    json.dumps(details) if details_dirty else None,
                    found_place[0] if found_place else None,
                    found_place[1] if found_place else None,
                    found_place[2] if found_place else None,
                    found_place[3] if found_place else None,
                    checked_at,
                    changed,
                    checked_at,
                    company_id,
                ),
            )
            database.commit()
            updated += 1
            print("Existing row updated in place.")
            if employer:
                shared._debug_run.lead(action="employer site added", id=company_id, title=search_title,
                                company=company.get("name"), original_source=source_url,
                                employer_site=details["employer_site"], view_link=career_url)
            elif employer_notes:
                shared._debug_run.note(f"row {company_id} ({company.get('name')}): " + "; ".join(employer_notes))
        except Error as error:
            database.rollback()
            failed += 1
            print("Could not update this existing row.")
            print(error)
        finally:
            if update_cursor is not None:
                update_cursor.close()

    try:
        expired = tidy_closed_jobs(database)
        if expired:
            print(f"Deleted {expired} job(s) closed for more than {CLOSED_KEEP_DAYS} days (saved jobs are kept).")
    except Error as error:
        print(f"Could not tidy closed jobs: {error}")
    # With a Brave Search key, a refresh also looks for employers' own postings of job-site listings.
    if brave_key() and not shared.stop_requested():
        try:
            found = company_check.check_listings(database, lambda query: search_web(query), fetch_text)
            print(f"Company-site check: found {found} on the employer's own site.")
        except Exception as error:
            print(f"Company-site check stopped early: {error}")
    database.close()

    print()
    print("=" * 60)
    print("UPDATE COMPLETE")
    print(f"Rows updated: {updated}")
    print(f"Rows rejected (wrong role or outside the U.S.): {rejected_count}")
    print(f"Rows failed: {failed}")
    shared._debug_run.finish(f"update complete: {updated} updated, {rejected_count} rejected, {failed} failed")
    shared._debug_run = None
