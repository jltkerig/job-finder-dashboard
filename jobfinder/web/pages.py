"""The pages themselves: Search, Dashboard, Settings (rejected listings, skips, block lists) and the
credibility guide.
"""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from flask import render_template, request

from jobfinder.profiles.profile_tools import resume_skill_suggestions, parse_work_history, uploaded_resume
from jobfinder.records.search_skips import TTL_HOURS, latest_decisions
from jobfinder.sources.job_listings import is_pdf_url
from jobfinder.web import blocklists
from jobfinder.web.application_files import RESUME_BUILDER_URL, add_application_files
from jobfinder.web import listing_queries
from jobfinder.web import profile_store
from jobfinder.web import schema
from jobfinder.web.blocklists import block_details
from jobfinder.web.core import app
from jobfinder.web.listing_queries import (
    add_current_distances,
    merge_duplicates,
    liked_titles,
    turned_down_titles,
    add_drive_times,
    add_job_fit,
    get_companies,
    get_dashboard_counts,
    get_kept_companies,
)
from jobfinder.web.profile_store import listing_skill_demand, profile_skill_suggestions
from jobfinder.web.schema import ensure_keep_column
from jobfinder.web.search_control import scraper_status
from jobfinder.web.search_history import get_search_history
from jobfinder.web.webfiles import RESUME_FOLDER, SEARCH_SKIPS_FILE, SKIPS_PER_PAGE


@app.route("/")
def home():
    now = datetime.now(ZoneInfo("America/New_York"))

    print()
    print("=" * 60)
    print("PAGE REFRESH — " f"{now.strftime('%B %d, %Y at %I:%M:%S %p %Z')}")
    print("=" * 60)

    ensure_keep_column()
    schema.ensure_job_tracking_columns()
    companies = merge_duplicates(get_companies())

    running, mode = scraper_status()
    profile = profile_store.get_user_profile()
    add_job_fit(companies, profile.get("skills", []))
    add_current_distances(companies, profile.get("cities", []))
    recent_search = get_search_history(limit=1)
    latest_search_at = recent_search[0].get("searched_at") if recent_search else None
    skipped = []
    if SEARCH_SKIPS_FILE.exists():
        try:
            for item in reversed(list(latest_decisions(SEARCH_SKIPS_FILE).values())):
                # Older searches recorded PDFs as skips; they are now ignored entirely.
                if item.get("reason") != "Passed" and not is_pdf_url(item.get("url")):
                    skipped.append(item)
                if len(skipped) >= 20:
                    break
        except (OSError, ValueError):
            skipped = []

    return render_template(
        "index.html",
        companies=companies,
        skipped=skipped,
        latest_search_at=latest_search_at,
        scraper_running=running,
        scraper_mode=mode or "",
        profile_cities=profile.get("cities", []),
        profile_job_titles=profile.get("job_titles", []),
        profile_state=profile.get("state", ""),
        profile_work_preferences=profile.get("work_preferences", []),
        profile_avoid_terms=profile.get("avoid_terms", []),
        applied_titles=[company.get("career_job_title") for company in companies
                        if company.get("application_status") in {"Applied", "Talking With Recruiter", "Interview"}
                        and company.get("career_job_title")],
        turned_down_titles=turned_down_titles(),
        liked_titles=liked_titles(),
    )


@app.route("/dashboard")
def user_dashboard():
    ensure_keep_column()
    schema.ensure_job_tracking_columns()
    status_filter = request.args.get("status", "").strip()[:30]
    state_filter = request.args.get("state", "").strip()[:100]
    title_filter = request.args.get("title", "").strip()[:255]
    sort_by = request.args.get("sort", "date_desc").strip()[:30]
    companies = get_kept_companies(status_filter, state_filter, title_filter, sort_by)
    profile = profile_store.get_user_profile()
    add_job_fit(companies, profile.get("skills", []))
    add_drive_times(companies, profile.get("home_zip"), profile.get("state") or "")
    add_application_files(companies)
    resume_skills = resume_skill_suggestions(RESUME_FOLDER, profile.get("skills", []))
    # With no work history saved yet, the form is filled in from the uploaded résumé (nothing is saved until Save Profile).
    resume_history = [] if profile.get("work_history") else parse_work_history(uploaded_resume(RESUME_FOLDER)[1])
    demanded_skills = [{"skill": skill, "count": count} for skill, count in listing_skill_demand(profile.get("skills", []))]
    return render_template(
        "user-dashboard.html",
        companies=companies,
        profile=profile,
        resume_builder_url=RESUME_BUILDER_URL,
        profile_version=profile_store.profile_version(profile),
        profile_changed=request.args.get("profile_changed", type=int),
        education_degrees=schema.EDUCATION_DEGREES,
        counts=get_dashboard_counts(),
        search_history=get_search_history(),
        skill_suggestions=profile_skill_suggestions(profile),
        resume_source=resume_skills[0], resume_skills=resume_skills[1],
        demanded_skills=demanded_skills,
        resume_history=resume_history,
        fit_updated=request.args.get("fit_updated", type=int),
        filters={"status": status_filter, "state": state_filter, "title": title_filter, "sort": sort_by},
    )


@app.route("/credibility-scores")
def credibility_scores():
    return render_template("credibility-scores.html")


@app.route("/rejected-listings")
def rejected_listings():
    schema.ensure_job_tracking_columns()
    decisions = latest_decisions(SEARCH_SKIPS_FILE)
    search_skips = []
    for event in reversed(list(decisions.values())):
        if event.get("reason") == "Passed" or is_pdf_url(event.get("url")):
            continue
        checked_at = event.get("checked_at") or ""
        next_check = "Next search"
        if checked_at and event.get("reason") in TTL_HOURS:
            try:
                expires = datetime.fromisoformat(checked_at.replace("Z", "+00:00")) + timedelta(hours=TTL_HOURS[event["reason"]])
                next_check = expires.astimezone(ZoneInfo("America/New_York")).strftime("%b %d, %Y %I:%M %p") if expires > datetime.now(timezone.utc) else "Next search"
            except (ValueError, TypeError):
                pass
        search_skips.append({**event, "next_check": next_check})
    # The skipped pages run to thousands, so only one page of them is sent (and searched on the server).
    skip_query = (request.args.get("skip_q") or "").strip()[:200]
    if skip_query:
        needle = skip_query.casefold()
        search_skips = [item for item in search_skips
                        if needle in " ".join(str(item.get(k) or "") for k in ("title", "reason", "url")).casefold()]
    skip_total = len(search_skips)
    skip_pages = max(1, -(-skip_total // SKIPS_PER_PAGE))
    try:
        skip_page = min(max(int(request.args.get("skip_page", 1)), 1), skip_pages)
    except ValueError:
        skip_page = 1
    search_skips = search_skips[(skip_page - 1) * SKIPS_PER_PAGE:skip_page * SKIPS_PER_PAGE]
    return render_template(
        "rejected-listings.html",
        companies=listing_queries.get_rejected_companies(),
        search_skips=search_skips, skip_total=skip_total, skip_page=skip_page, skip_pages=skip_pages, skip_query=skip_query,
        blocked_domains=blocklists.get_blocked_domains(),
        blocked_companies=blocklists.get_blocked_companies(),
        blocked_domain_info=block_details("domains", blocklists.get_blocked_domains()),
        blocked_company_info=block_details("companies", blocklists.get_blocked_companies()),
        company_notice=request.args.get("company_notice", ""),
        domain_notice=request.args.get("domain_notice", ""),
    )
