"""Big employers' own career boards (watched_employers.json) and boards found while searching the web.
"""

import json
import re
import time

import requests

from jobfinder.profiles.profile_tools import listing_skills
from jobfinder.records.board_health import HEALTH_FILE as BOARD_HEALTH_FILE
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
from jobfinder.sources.ats_discovery import (
    identify as identify_board,
    identify_unreadable,
    pretty_name as board_name,
)
from jobfinder.sources.employer_jobs import (
    SOURCE_TYPE as EMPLOYER_SOURCE,
    Http as EmployerHttp,
    Employer,
    config_key,
    load_employers,
    posting_domain,
    record_board_result,
    save_discovered,
)
from jobfinder.sources.job_listings import canonical_url, matching_title as matching_job_title
from jobfinder.search.board_readahead import BoardReadAhead
from jobfinder.search.run_state import title_label

MAX_BOARDS_PER_RUN = 20


def prepare_employers(run):
    """Load the watched employers (the first board changes every hour) and note which boards are already known."""
    employer_http = EmployerHttp()
    run.employer_http = employer_http
    known_employers = load_employers()
    _last_matches = {}
    try:
        with open(BOARD_HEALTH_FILE, encoding="utf-8") as health_file:
            boards = json.load(health_file).get("boards") or []
        for board in (boards.values() if isinstance(boards, dict) else boards):
            _last_matches[str(board.get("name"))] = int(board.get("matches") or 0)
    except (OSError, ValueError, AttributeError, TypeError):
        pass
    if known_employers:
        # Start at a different board each hour so a full quota does not always come from the first few in the list.
        turn = int(time.time() // 3600) % len(known_employers)
        known_employers = known_employers[turn:] + known_employers[:turn]
        if shared.MAX_SEARCH_RESULTS == 1:
            # A replacement for one Top 10 pick: the boards that had matching openings last time are tried first, so the
            # one job it needs is found without reading the boards that never match.
            known_employers.sort(key=lambda employer: -_last_matches.get(employer.name, 0))
    known_boards = {config_key(employer.config) for employer in known_employers}
    run.known_employers, run.known_boards = known_employers, known_boards


def search_employer(run, employer, per_employer_cap, total_cap):
    """Search one employer's board and save passing openings; returns how many were saved."""
    database, job_titles, state, employer_http = run.database, run.job_titles, run.state, run.employer_http
    selected_states, statewide_states, city_targets = run.selected_states, run.statewide_states, run.city_targets
    seen_openings, rejected_urls = run.seen_openings, run.rejected_urls
    if run.employer_saved_total >= total_cap or run.companies_saved >= shared.MAX_SEARCH_RESULTS or shared.stop_requested():
        return 0
    print(f"Checking {employer.name} careers...")
    try:
        employer_openings = run.read_ahead.openings(employer) if run.read_ahead else None  # already read while the last board was checked
        if employer_openings is None:
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
        if (run.companies_saved >= shared.MAX_SEARCH_RESULTS or run.employer_saved_total >= total_cap
                or employer_saved >= per_employer_cap):
            break
        job_url = canonical_url(opening["url"])
        run.candidate_number += 1
        run.results_checked += 1
        print(f"Checking result {run.candidate_number}: {opening['title'][:75]} ({employer.name})")
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
        matched_title = title_label(next((wanted for wanted in job_titles + employer.extra_titles
                              if matching_job_title(opening["title"], [wanted])), job_titles[0]))
        details = {"schedule": opening["schedule"], "salary": opening["salary"], "posted": opening["posted"],
                   "evidence": opening["evidence"], "location": opening["location"], "matched_title": matched_title,
                   "remote_limited_to": sorted(outcome["remote_limited_to"]),
                   "verification": "Company's own careers site",
                   "source": employer.adapter.base if hasattr(employer.adapter, "base") else posting_domain(employer.domain, job_url)}
        inserted = storage.save_company(database, employer.name, opening["title"], outcome["detail_score"],
                                posting_domain(employer.domain, job_url),
                                job_url, job_url, outcome["country"],
                                str(outcome["state_name"] or "")[:100] or None, outcome["location"]["score"],
                                city=outcome["city"], latitude=outcome["lat"], longitude=outcome["lon"],
                                distance_miles=outcome["miles"], work_arrangement=outcome["arrangement"],
                                skills=listing_skills(opening["html"]), source_type=EMPLOYER_SOURCE,
                                listing_details=details)
        record_decision(SEARCH_SKIPS_FILE, _skip_decisions, "Passed", job_url, opening["title"])
        run.passed_count += 1
        print(f"Passed validation: {run.passed_count} · {opening['title']} — {', '.join(opening['evidence'])}", flush=True)
        debug_lead(action="saved" if inserted else "already saved (updated)", source=f"{employer.name} careers",
                   title=opening["title"], company=employer.name, url=job_url, location=opening["location"],
                   all_locations=opening["locations"], matched_title=matched_title, arrangement=outcome["arrangement"],
                   city=outcome["city"], distance_miles=outcome["miles"], remote_limited_to=sorted(outcome["remote_limited_to"]),
                   posted=opening["posted"])
        if inserted:
            run.companies_saved += 1
            employer_saved += 1
            run.employer_saved_total += 1
            print(f"Saved job {run.companies_saved}/{shared.MAX_SEARCH_RESULTS}: {job_url}")
        else:
            run.existing_companies += 1
    _board_health.note(employer.name, "Discovered board" if employer.discovered else "Employer board",
                       "ok" if employer_openings else "no matches", len(employer_openings), employer_saved)

    return employer_saved


def discover_board(run, page_url, company_hint=""):
    """A web result on a hiring platform (UltiPro, Greenhouse, ...): search that employer's whole board."""
    known_boards, unreadable_boards = run.known_boards, run.unreadable_boards
    if run.boards_discovered >= MAX_BOARDS_PER_RUN or shared.stop_requested():
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
    run.boards_discovered += 1
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
    search_employer(run, employer, 3, shared.MAX_SEARCH_RESULTS)


def search_employer_boards(run):
    """Boards are read a few at a time ("Pages downloaded at once" on the Tuning page); each board's openings are still
    checked and saved one board at a time, in the same order."""
    known_employers, job_titles = run.known_employers, run.job_titles
    # Employer boards share up to half of a search's jobs, two per board, so several get a turn.
    employer_cap = max(1, shared.MAX_SEARCH_RESULTS // 2)
    # Boards are read a few at a time ("Pages downloaded at once" on the Tuning page); each board's openings are still
    # checked and saved one board at a time, in the same order.
    # A replacement for one pick needs a single job and wants it soon: more boards are read at once.
    workers = min(max(shared.PREFETCH_WORKERS, 10) if shared.MAX_SEARCH_RESULTS == 1 else shared.PREFETCH_WORKERS,
                  len(known_employers))
    if workers > 1:
        run.read_ahead = BoardReadAhead(known_employers, job_titles, workers)
    try:
        with timed("Employer career boards"):
            for employer in known_employers:
                search_employer(run, employer, 2, employer_cap)
                if run.employer_saved_total >= employer_cap:
                    break
    except Exception as error:
        print(f"Employer career search stopped early: {error}", flush=True)
    finally:
        if run.read_ahead:
            run.read_ahead.close()
            run.read_ahead = None
