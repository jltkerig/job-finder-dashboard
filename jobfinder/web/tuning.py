"""The Tuning page: the search settings you can change and where they are saved."""

from datetime import datetime
import json
import os
from pathlib import Path
from urllib.parse import quote

from flask import redirect, render_template, request

from jobfinder.records.board_health import STATUSES as HEALTH_STATUSES, read_health
from jobfinder.web import webfiles
from jobfinder.web.core import app, log_error_code

# Search tuning options that live in settings.json: key -> (label, help, kind, minimum, maximum, default).
TUNING_FIELDS = {
    "searxng_timeout_minutes": ("Search time limit (minutes)", "A search stops after this long.", int, 1, 240, 60),
    "max_search_results": ("Jobs to find per search", "A search stops once it has saved this many new jobs.", int, 1, 100, 10),
    "max_search_pages": ("Result pages per query", "How many pages of results to read for each search query.", int, 1, 50, 20),
    "request_delay_seconds": ("Pause between requests to one site (seconds)", "Lower is faster; keep at 1 or more to stay polite.", float, 0, 10, 1),
    "search_query_delay_seconds": ("Pause between search-engine queries (seconds)", "Too low can get the engines to rate-limit Job Finder.", float, 0, 10, 2),
    "website_timeout_seconds": ("Wait for a slow page (seconds)", "A page that has not answered by then is skipped.", int, 5, 60, 15),
    "parallel_page_fetches": ("Pages downloaded at once", "More is faster but uses more of your connection.", int, 1, 12, 6),
    "auto_search_hour": ("Daily search starts after (hour, 0–23)", "Used when the daily search is on. 8 means 8 a.m.", int, 0, 23, 8),
    "stop_after_empty_queries": ("Stop after this many empty queries in a row", "Many empty queries usually mean the engines are refusing us.", int, 2, 30, 8),
}

TUNING_SWITCHES = {
    "daily_auto_search": ("Search every day and queue the best jobs", "Reruns your last search once a day while Job Finder is open. Top 10 then queues its strong picks on the Dashboard to apply to. Nothing is submitted for you.", False),
    "usa_only": ("U.S. jobs only", "Skip jobs that are outside the United States or unverified.", True),
    "exclude_internships": ("Skip internships and co-ops", "Leave out jobs titled intern, internship or co-op.", True),
    "related_titles": ("Also match closely related titles", "Recognise titles such as Multimedia Designer or Production Artist when you typed Designer or Production Specialist.", True),
    "start_docker_automatically": ("Start Docker automatically", "Starts Docker Desktop for the web search.", True),
    "stop_docker_when_finished": ("Stop Docker when finished", "Closes Docker Desktop after the search.", True),
}


def read_tuning_settings():
    try:
        return json.loads(webfiles.SETTINGS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def apply_tuning_form(form, current):
    """Return (new settings, error). Only the known options change; every other key in the file is kept."""
    updated = dict(current)
    for key, (label, _, kind, low, high, default) in TUNING_FIELDS.items():
        if key not in form:  # an older form without this option keeps what's saved
            updated[key] = current.get(key, default)
            continue
        raw = str(form.get(key, "")).strip()
        try:
            value = kind(raw)
        except ValueError:
            return None, f"{label} must be a number."
        if not low <= value <= high:
            return None, f"{label} must be between {low} and {high}."
        updated[key] = int(value) if kind is int or float(value).is_integer() else value
    if "brave_api_key" in form:  # blank clears it; Job Finder then uses SearXNG
        updated["brave_api_key"] = str(form.get("brave_api_key", "")).strip()[:200]
    for key in TUNING_SWITCHES:
        updated[key] = form.get(key) == "on"
    return updated, None


@app.route("/tuning")
def tuning_page():
    current = read_tuning_settings()
    health = read_health()
    if health:
        try:
            health["updated_text"] = datetime.fromisoformat(health["updated"]).astimezone().strftime("%b %d, %Y %I:%M %p")
        except (KeyError, ValueError):
            health["updated_text"] = health.get("updated", "")
    return render_template("tuning.html", fields=TUNING_FIELDS, switches=TUNING_SWITCHES, values=current,
                           health=health, statuses=HEALTH_STATUSES, saved=request.args.get("saved"),
                           error=request.args.get("error"))


@app.route("/tuning/settings", methods=["POST"])
def save_tuning_settings():
    updated, error = apply_tuning_form(request.form, read_tuning_settings())
    if error:
        return redirect("/tuning?error=" + quote(error) + "#search-settings")
    temporary = Path(str(webfiles.SETTINGS_FILE) + ".tmp")
    try:
        temporary.write_text(json.dumps(updated, indent="\t") + "\n", encoding="utf-8")
        os.replace(temporary, webfiles.SETTINGS_FILE)
    except OSError as error:
        log_error_code("E4101", f"Could not save settings.json: {error}")
        return redirect("/tuning?error=" + quote("Could not save the settings file.") + "#search-settings")
    return redirect("/tuning?saved=1#search-settings")
