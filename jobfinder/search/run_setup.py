"""The start of a search: the database, the typed titles and the chosen places.
"""

import json


from jobfinder.profiles.onet_data import spelling_fix
from jobfinder.search import geo
from jobfinder.search import shared
from jobfinder.search import storage
from jobfinder.search.refresh import (
    close_expired_listings_quietly,
    recheck_system_rejections_quietly,
    refresh_job_fit_quietly,
)
from jobfinder.search.relevance import expand_job_titles
from jobfinder.search.shared import (
    BLOCKED_COMPANIES,
    BLOCKED_DOMAINS,
    TIMEOUT,
    USA_ONLY,
    related_family_titles,
    timed,
)
from jobfinder.search.usa_location import US_STATES, selected_state_codes
from jobfinder.sources.job_listings import ANY_TITLE

from jobfinder.search.run_state import SearchRun


def start_search(job_title=None, state=None, cities_json=None):
    """Open the database, read the titles and places, and return the SearchRun; None when the search cannot start."""
    print()
    print("================================")
    print("       PERSONAL JOB FINDER")
    print("================================")
    print()

    with timed("Opening the database"):
        database = storage.connect_database()

        if database is None:
            return

        if not storage.ensure_database_schema(database):
            database.close()
            return
    with timed("Start-up check: Job Fit of saved listings"):
        refresh_job_fit_quietly(database)
    with timed("Start-up check: listings rejected earlier"):
        recheck_system_rejections_quietly(database)
    with timed("Start-up check: listings past their closing date"):
        close_expired_listings_quietly(database)

    rejected_urls = storage.rejected_posting_urls(database)

    print()
    print("Connected to job_finder database.")

    print()

    if job_title is None:  # only the command line asks; an empty title from the dashboard means any job
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

    corrected_titles = []
    for index, typed_title in enumerate(job_titles):
        fixed = spelling_fix(typed_title)
        if fixed.casefold() != typed_title.casefold():
            print(f'Spelling corrected: "{typed_title}" -> "{fixed}"')
            corrected_titles.append((typed_title, fixed))
            job_titles[index] = fixed
    selected_titles = list(dict.fromkeys(job_titles))
    job_titles = expand_job_titles(selected_titles) if selected_titles else []
    if len(job_titles) > len(selected_titles):
        print("Related O*NET search titles: " + ", ".join(job_titles[len(selected_titles):]))
    # Web queries use the typed and O*NET titles; the related families below are only used to recognise openings.
    query_titles = list(job_titles)
    related_extras = [title for title in (related_family_titles(selected_titles) if selected_titles else []) if title.casefold() not in {t.casefold() for t in job_titles}]
    job_titles = job_titles + related_extras
    if related_extras:
        print("Also matching closely related titles: " + ", ".join(related_extras))
    # No job title: any real job near the chosen places.
    any_title = not job_titles
    if any_title:
        job_titles = [ANY_TITLE]
        print("No job title: looking for any jobs in the area.")

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
    with timed("Looking up your cities"):
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
    return SearchRun(database, state, cities, selected_titles, job_titles, query_titles, any_title,
                     selected_states, statewide_states, city_targets, rejected_urls)
