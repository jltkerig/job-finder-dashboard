"""The web search: Brave queries, each result's page, its career pages and the openings on them.
"""

from urllib.parse import urljoin

from bs4 import BeautifulSoup

from jobfinder.profiles.profile_tools import listing_skills
from jobfinder.records.search_skips import cached_skip, record_decision
from jobfinder.search import session
from jobfinder.search import fetching
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
from jobfinder.search.geo import _ZIP_CODE
from jobfinder.search.relevance import apply_link_closed
from jobfinder.search.searching import search_blocked_message, search_web
from jobfinder.search.shared import (
    BLOCKED_COMPANIES,
    EMPTY_QUERY_LIMIT,
    SEARCH_SKIPS_FILE,
    SITE_QUERY_PAGES,
    _skip_decisions,
    debug_lead,
    debug_skip,
    record_skip,
)
from jobfinder.search.usa_location import US_STATES
from jobfinder.sources.ats_feeds import public_board_links
from jobfinder.sources.employer_site import is_third_party
from jobfinder.sources.job_listings import (
    canonical_url,
    extract_jobs,
    is_article_page,
    is_pdf_url,
    job_links,
    pagination_links,
    matching_title as matching_job_title,
)
from jobfinder.search.area_queries import area_queries

from jobfinder.search.run_employers import discover_board
from jobfinder.search.run_state import title_label


def web_queries(run):
    """Every search the web step will run: board-site searches first, then wider ones per title and place."""
    selected_titles, query_titles, cities, state, any_title = run.selected_titles, run.query_titles, run.cities, run.state, run.any_title
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
    if any_title:
        search_queries = area_queries(state, city_names, shared.JOB_BOARD_SITES)
    return search_queries


def save_opening(run, opening, page, url, title, company_name, search_query):
    """Check one opening found on a web result's page and save it when it passes."""
    database, state, job_titles = run.database, run.state, run.job_titles
    selected_states, statewide_states, city_targets = run.selected_states, run.statewide_states, run.city_targets
    seen_openings, rejected_urls = run.seen_openings, run.rejected_urls
    job_url = canonical_url(opening["url"])
    if not job_url or job_url in seen_openings:
        return
    if job_url in rejected_urls:
        debug_skip("Previously rejected", job_url, opening["title"])
        return
    seen_openings.add(job_url)
    if is_blocked_domain(get_domain(job_url)):
        debug_skip("Blocked domain", job_url, opening["title"])
        return
    name = tidy_company_name(opening["company"]) or company_name
    if name.casefold() in BLOCKED_COMPANIES:
        debug_skip("Blocked company", job_url, opening["title"])
        return
    details = {key: opening.get(key) for key in ("schedule", "salary", "posted", "evidence", "location")}
    details["source"] = url
    details["matched_title"] = title_label(next((wanted for wanted in job_titles if matching_job_title(opening["title"], [wanted])), job_titles[0]))
    outcome = judging.assess_opening(database, opening, page.text, job_url, state, selected_states,
                             statewide_states, city_targets)
    if outcome["skip"]:
        record_skip(outcome["skip"], job_url, opening["title"])
        return
    arrangement, location, detail_score = outcome["arrangement"], outcome["location"], outcome["detail_score"]
    country, state_name, remote_limited_to = outcome["country"], outcome["state_name"], outcome["remote_limited_to"]
    details["remote_limited_to"] = sorted(remote_limited_to)
    city, lat, lon, miles = outcome["city"], outcome["lat"], outcome["lon"], outcome["miles"]
    # A repost on a job board can outlive the real opening; check where Apply leads.
    closed = apply_link_closed(job_url, page.text) if is_third_party(job_url, name) else None
    if closed:
        record_skip("Job is closed (expired)", job_url, opening["title"])
        debug_skip(f"closed: {closed['reason']}", closed["apply_link"], opening["title"])
        return
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
    run.passed_count += 1
    print(f"Passed validation: {run.passed_count} · {opening['title']} — {', '.join(details.get('evidence') or ['individual opening'])}", flush=True)
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
        run.companies_saved += 1
        print(f"Saved job {run.companies_saved}/{shared.MAX_SEARCH_RESULTS}: {job_url}")
    else:
        run.existing_companies += 1


def check_result(run, result, search_query):
    """Read one web search result: its page, its career pages and every opening on them."""
    job_titles, fetched_pages = run.job_titles, run.fetched_pages
    run.candidate_number += 1
    title = str(result.get("title") or "")
    url = canonical_url(result.get("url") or "")
    if not url or not is_valid_url(url):
        return
    domain = get_domain(url)
    if is_blocked_domain(domain) or has_blocked_country_domain(domain):
        run.blocked_sites += 1
        record_skip("Blocked domain", url, title)
        return
    print(f"Checking result {run.candidate_number}: {title[:75]}", flush=True)
    recent_skip = cached_skip(_skip_decisions, url)
    if recent_skip:
        print(f"Skipped (recent check: {recent_skip['reason']}): {title[:70]} {url[:120]}", flush=True)
        return
    discover_board(run, url, title)
    run.websites_checked += 1
    landing = fetching.safe_request(url)
    if landing is None:
        record_skip("Page unavailable", url, title)
        return
    landing_soup = BeautifulSoup(landing.text, "html.parser")
    if is_directory_or_marketplace_result(domain, title, landing.text):
        record_skip("Directory or marketplace page", url, title)
        return
    if is_article_page(landing_soup) or is_student_employment_overview(url, landing.text):
        record_skip("Article or student employment guide", url, title)
        return
    company_name = extract_company_name(landing_soup, title, domain)
    if company_name.strip().casefold() in BLOCKED_COMPANIES:
        debug_skip("Blocked company", url, title)
        return
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
    while pending and len(checked) < 18 and run.companies_saved < shared.MAX_SEARCH_RESULTS and session.web_search_ok():
        page_url = pending.pop(0)
        if page_url != url:
            discover_board(run, page_url, company_name)
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
            save_opening(run, opening, page, url, title, company_name, search_query)
    if not checked:
        run.no_career_page += 1


def search_web_pages(run, search_queries):
    """Run each query page by page and check every new result, until the quota is full, the engines refuse or a stop is asked."""
    seen_result_urls = run.seen_result_urls
    empty_query_streak = 0
    run.blocked_message = None

    for search_query in search_queries:
        if run.companies_saved >= shared.MAX_SEARCH_RESULTS or run.blocked_message or not session.web_search_ok():
            break

        print()
        print(f'Search variation: "{search_query}"')
        if shared._debug_run is not None:
            shared._debug_run.query(search_query)

        empty_or_repeating_pages = 0
        dry_pages = 0

        query_pages = SITE_QUERY_PAGES if search_query.startswith("site:") else shared.MAX_SEARCH_PAGES
        for page in range(1, query_pages + 1):
            if run.companies_saved >= shared.MAX_SEARCH_RESULTS or not session.web_search_ok():
                break

            results = search_web(search_query, page)
            if shared._debug_run is not None:
                shared._debug_run.query_page(len(results or []))
            if page == 1:
                # Many queries in a row with no results at all means the engines are refusing us.
                empty_query_streak = 0 if results else empty_query_streak + 1
                if empty_query_streak >= EMPTY_QUERY_LIMIT:
                    run.blocked_message = search_blocked_message(empty_query_streak)
                    print()
                    print(run.blocked_message, flush=True)
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
            passed_before = run.passed_count
            seen_result_urls.update(result.get("url") for result in new_results)
            run.pages_checked += 1
            # PDFs are ignored outright: not fetched, counted, or recorded as skips.
            web_results = [result for result in new_results if not is_pdf_url(result.get("url"))]
            pdfs_ignored = len(new_results) - len(web_results)
            run.results_checked += len(web_results)
            print(f"Checking search page {page} ({len(web_results)} new results"
                  + (f", {pdfs_ignored} PDFs ignored" if pdfs_ignored else "") + ")...")

            # Download this page's results in parallel; the checks below then read them from the cache.
            prefetch_pages([url for url in (canonical_url(result.get("url") or "") for result in web_results)
                            if url and not is_blocked_domain(get_domain(url)) and not has_blocked_country_domain(get_domain(url))
                            and not cached_skip(_skip_decisions, url)][:24])

            for result in web_results:
                if run.companies_saved >= shared.MAX_SEARCH_RESULTS or not session.web_search_ok():
                    break
                check_result(run, result, search_query)

            dry_pages = 0 if run.passed_count > passed_before else dry_pages + 1
            if mostly_seen:
                print("Most results on this page were already seen; moving to the next query.", flush=True)
                break
            if dry_pages >= 3:
                print("Three search pages in a row gave no matching jobs; moving to the next query.", flush=True)
                break
