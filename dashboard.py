"""Job Finder's web pages: start this file (python dashboard.py, or start.ps1) and open http://127.0.0.1:5000.

This file only puts the app together and starts it. The pages and what they do live in jobfinder/web/:

    core.py             the Flask app, security checks, error codes, compression, date filters
    pages.py            Search, Dashboard, Settings and the credibility guide
    profile_routes.py   saving your profile, reading a résumé, title and city suggestions
    listing_actions.py  the buttons on a listing: reject, restore, keep, block, notes
    top_picks_routes.py Top 10 Picks: requirements, posting preview, ratings
    search_control.py   starting, refreshing, importing and stopping searches, and their progress
    blocklists.py       blocked domains and companies
    tuning.py           the Tuning page
    update_checks.py    looking for and installing a newer Job Finder
    extension_api.py    what the browser extension asks for
    application_files.py the resumes and cover letters made for a saved job
    (profile_store, listing_queries, search_history, schema: reading and writing the database)
"""
from jobfinder.web import (application_files, auto_apply, blocklists, clear_results, extension_api, listing_actions, listing_queries, pages, top_picks_routes, profile_routes,  # noqa: F401
                           profile_store, schema, search_control, search_history, tuning, update_checks)
from jobfinder.web.core import app

# The version lives here, in one place. release.py raises it, update.ps1 and start.ps1 read it, and the pages show it.
APP_VERSION = "1.1.237"

app.config.update(APP_VERSION=APP_VERSION)


def prepare_database():
    schema.initialize_database()
    schema.ensure_keep_column()
    schema.ensure_job_tracking_columns()
    schema.ensure_profile_tables()
    schema.tidy_expired_closed_jobs()


if __name__ == "__main__":
    prepare_database()
    auto_apply.start_scheduler()
    app.run(host="127.0.0.1", port=5000, debug=False, use_reloader=False)
