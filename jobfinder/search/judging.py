"""Judging one job opening: does it fit your titles, is it in the United States, and is it close enough?"""

from bs4 import BeautifulSoup
from jobfinder.search import shared
from jobfinder.search.company_names import OFFICIAL_BOARD_CREDIBILITY, on_official_board
from jobfinder.search.company_site import score_career_page
from jobfinder.search.geo import distance_to_city_targets
from jobfinder.search.shared import CAREER_CREDIBILITY_THRESHOLD, USA_ONLY, is_internship
from jobfinder.search.usa_location import US_STATES, US_STATE_ABBREVIATIONS, analyze_usa_location, detect_work_arrangement, find_state_from_text, remote_state_restrictions
from jobfinder.sources.employer_site import is_third_party
from jobfinder.sources.job_listings import excludes_us


def assess_opening(database, opening, page_html, job_url, search_state, selected_states, statewide_states, city_targets):
    """The U.S., location and remote checks every source shares (web pages, employer career sites).

    Returns a dict. outcome["skip"] is the reason the job is out, or None when it passes; the rest
    (arrangement, location analysis, city, distance, ...) is what gets saved with it.
    """
    text = opening["description"] or BeautifulSoup(page_html, "html.parser").get_text(" ", strip=True)
    arrangement = opening["type"] or detect_work_arrangement(opening["title"], page_html)
    outcome = {"skip": None, "arrangement": arrangement, "location": None, "detail_score": 0, "country": None,
               "state_name": None, "remote_limited_to": set(), "city": None, "lat": None, "lon": None, "miles": None}
    if is_internship(opening.get("title"), opening.get("schedule")):
        outcome["skip"] = "Internship"
        return outcome
    if excludes_us(opening["location"], text):
        outcome["skip"] = "Posting restricts applicants outside the US"
        return outcome
    location = analyze_usa_location(page_html, extra_text=opening["location"], source_label=f"job posting {job_url}", page_url=job_url)
    # An individual opening was already confirmed, so it meets the career-credibility threshold.
    detail_score = max(CAREER_CREDIBILITY_THRESHOLD, score_career_page(job_url, page_html)["score"])
    # A confirmed opening on the employer's own applicant-system board (or read from an employer careers API)
    # is strong evidence even when the page is script-rendered and scores little on its text.
    if on_official_board(job_url) or any("careers site (" in str(line) for line in opening.get("evidence") or ()):
        detail_score = max(detail_score, OFFICIAL_BOARD_CREDIBILITY)
    # The listing's own location beats anything found elsewhere on the page.
    state_name = (find_state_from_text(opening["location"] or "") or location["state"] or opening["location"] or None)
    code = US_STATES.get(str(state_name or "").casefold(), str(state_name or "").upper())
    outcome.update(location=location, detail_score=detail_score, country=location["country"], state_name=state_name)
    # Remote jobs pass any location filter unless the listing says the applicant must live in particular
    # states. A statewide match passes even when city radii are also selected.
    within_radius, skip_reason = True, "Outside selected location"
    if arrangement == "Remote":
        limited_to = remote_state_restrictions(text, code if code in US_STATE_ABBREVIATIONS else None,
                                               opening.get("locations") or [opening.get("location")])
        limited_to |= set(opening.get("remote_states") or ())
        outcome["remote_limited_to"] = limited_to
        if limited_to and selected_states and not (limited_to & selected_states):
            within_radius = False
            skip_reason = "Remote job limited to residents of " + ", ".join(sorted(limited_to))
    elif statewide_states and code in statewide_states:
        pass
    elif city_targets:
        within_radius, outcome["city"], outcome["lat"], outcome["lon"], outcome["miles"] = distance_to_city_targets(
            database, page_html, opening["location"] or opening["title"], search_state, city_targets,
            allow_footer=not is_third_party(job_url, opening.get("company") or ""))
    elif statewide_states:
        within_radius = False
    if not within_radius:
        outcome["skip"] = skip_reason
    elif USA_ONLY and location["score"] < shared.USA_CREDIBILITY_THRESHOLD:
        outcome["skip"] = "US eligibility unverified"
    return outcome
