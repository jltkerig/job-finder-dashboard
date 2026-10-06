"""Auto-apply, part 1: a daily search on a timer and the Apply queue.

The timer reruns the last search once a day after the hour set on the Tuning page. The next time the Search page
opens, Top 10 runs by itself and queues its strong picks (no hard gaps). Queued jobs are kept on the Dashboard,
where Apply opens the posting; nothing is ever submitted for you.
"""

from datetime import datetime
import json
import os
from pathlib import Path
import threading
import time

from flask import jsonify, request
from mysql.connector import Error

from jobfinder import db
from jobfinder.web import webfiles
from jobfinder.web.core import api_error, app, log_error_code
from jobfinder.web.schema import ensure_job_tracking_columns, ensure_keep_column
from jobfinder.web.tuning import read_tuning_settings

CHECK_EVERY_SECONDS = 600
QUEUE_DONE = ("Applied", "Talking With Recruiter", "Interview", "Rejected", "Closed")


def write_settings(changes):
    """Merge changes into settings.json, keeping every other key."""
    settings = read_tuning_settings()
    settings.update(changes)
    temporary = Path(str(webfiles.SETTINGS_FILE) + ".tmp")
    temporary.write_text(json.dumps(settings, indent="\t") + "\n", encoding="utf-8")
    os.replace(temporary, webfiles.SETTINGS_FILE)


def search_due(settings, now):
    """True when the daily search is on, its hour has come and it hasn't run today."""
    if not settings.get("daily_auto_search"):
        return False
    try:
        hour = int(settings.get("auto_search_hour", 8))
    except (TypeError, ValueError):
        hour = 8
    return now.hour >= hour and settings.get("auto_search_last_date") != now.date().isoformat()


def auto_queue_pending(settings):
    """True when a daily search ran and its picks haven't been queued yet."""
    last = settings.get("auto_search_last_date")
    return bool(last) and settings.get("auto_queue_date") != last


def run_daily_search():
    """Start the last search again. Returns True when it started (or one was already running)."""
    from jobfinder.web import search_control
    from jobfinder.web.search_history import get_search_history

    history = get_search_history(limit=1)
    if not history:
        return False
    search = history[0]
    try:
        cities = json.loads(search.get("cities_json") or "[]")
    except (TypeError, ValueError):
        cities = []
    with app.app_context():
        response = search_control.launch_search_process(search.get("job_title"), search.get("state"), cities)
    body = (response[0] if isinstance(response, tuple) else response).get_json() or {}
    # Someone else's search is running: try again on the next check rather than marking today done.
    return body.get("status") == "started"


def _tick():
    settings = read_tuning_settings()
    now = datetime.now(webfiles.LOCAL_TIMEZONE)
    if search_due(settings, now) and run_daily_search():
        write_settings({"auto_search_last_date": now.date().isoformat()})


def _loop():
    while True:
        try:
            _tick()
        except Exception as error:  # the timer must keep going
            log_error_code("E3240", f"Daily search could not start: {error}")
        time.sleep(CHECK_EVERY_SECONDS)


def start_scheduler():
    threading.Thread(target=_loop, name="daily-search", daemon=True).start()


def _details(value):
    try:
        return json.loads(value or "{}") or {}
    except (TypeError, ValueError):
        return {}


def get_apply_queue():
    """Queued jobs not yet applied to, oldest first."""
    connection = cursor = None
    try:
        connection = db.connect()
        cursor = connection.cursor(dictionary=True)
        cursor.execute("SELECT id, name, career_job_title, career_url, source_url, application_status, listing_details "
                       "FROM companies WHERE listing_details LIKE %s AND COALESCE(is_rejected, 0) = 0 ORDER BY id",
                       ('%"apply_queued"%',))
        rows = cursor.fetchall()
    except Error as error:
        log_error_code("E3241", f"Could not read the Apply queue: {error}")
        return []
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()
    queue = []
    for row in rows:
        if _details(row.pop("listing_details")).get("apply_queued") and row.get("application_status") not in QUEUE_DONE:
            row["url"] = row.get("career_url") or row.get("source_url") or ""
            queue.append(row)
    return queue


def _ids(values):
    try:
        ids = [int(value) for value in values]
    except (TypeError, ValueError):
        return None
    return ids if 0 < len(ids) <= 50 and all(value > 0 for value in ids) else None


@app.route("/apply-queue", methods=["POST"])
def update_apply_queue():
    """{company_ids, queued, auto}: queue jobs (they're kept on the Dashboard too) or take them off the queue.
    auto marks the daily search's picks as handled."""
    ensure_keep_column()
    ensure_job_tracking_columns()
    data = request.get_json(silent=True) or {}
    ids = _ids(data.get("company_ids") or [])
    queued = bool(data.get("queued", True))
    if data.get("auto"):
        settings = read_tuning_settings()
        write_settings({"auto_queue_date": settings.get("auto_search_last_date")})
        if not data.get("company_ids"):
            return jsonify({"status": "saved", "count": 0})
    if ids is None:
        return jsonify({"status": "error", "message": "Invalid listing selection."}), 400
    connection = cursor = None
    try:
        connection = db.connect()
        cursor = connection.cursor(dictionary=True)
        marks = ",".join(["%s"] * len(ids))
        cursor.execute(f"SELECT id, listing_details FROM companies WHERE id IN ({marks})", ids)
        for row in cursor.fetchall():
            details = _details(row.get("listing_details"))
            if queued:
                details["apply_queued"] = True
            else:
                details.pop("apply_queued", None)
            cursor.execute("UPDATE companies SET listing_details = %s WHERE id = %s", (json.dumps(details), row["id"]))
        if queued:
            cursor.execute(f"UPDATE companies SET is_kept = 1, application_status = 'Saved' WHERE id IN ({marks}) "
                           "AND COALESCE(application_status, 'None') IN ('None', '')", ids)
        connection.commit()
    except Error as error:
        return api_error("E3242", f"Could not update the Apply queue: {error}", 500)
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()
    return jsonify({"status": "saved", "count": len(ids)})


@app.route("/apply-queue/applied/<int:company_id>", methods=["POST"])
def mark_queue_applied(company_id):
    """You submitted it yourself: mark it Applied, which takes it off the queue."""
    ensure_job_tracking_columns()
    connection = cursor = None
    try:
        connection = db.connect()
        cursor = connection.cursor()
        cursor.execute("UPDATE companies SET is_kept = 1, application_status = 'Applied' WHERE id = %s", (company_id,))
        connection.commit()
    except Error as error:
        return api_error("E3243", f"Could not mark it applied: {error}", 500)
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()
    return jsonify({"status": "applied", "company_id": company_id})
