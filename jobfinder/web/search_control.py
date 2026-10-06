"""Starting, refreshing, importing and stopping searches (the job_finder.py process) and reporting
their progress.
"""

from datetime import datetime
import json
import os
import re
import subprocess
import sys
import threading
import time

from flask import jsonify, request

from jobfinder.records.capture_import import capture_dirs, move_pending, pending_files
from jobfinder.web import profile_store
from jobfinder.web import webfiles
from jobfinder.web.core import api_error, app, log_error_code
from jobfinder.web.search_history import get_search_history, record_search_history
from jobfinder.web.tuning import read_tuning_settings
from jobfinder.web.webfiles import BASE_DIR, GRACEFUL_STOP_SECONDS, JOB_FINDER_PATH

scraper_process = None
scraper_mode = None
scraper_last_error = None
scraper_lock = threading.Lock()
scraper_started_at = None
scraper_stopping = False


def scraper_status():
    global scraper_process, scraper_mode, scraper_last_error, scraper_stopping

    with scraper_lock:
        if scraper_process is None:
            return False, None

        exit_code = scraper_process.poll()
        if exit_code is None:
            return True, scraper_mode

        was_stopping = scraper_stopping
        scraper_stopping = False
        if exit_code != 0 and not was_stopping:
            scraper_last_error = (
                f"Job Finder stopped with exit code {exit_code}. "
                f"Details were saved to {webfiles.SCRAPER_LOG_FILE.name}."
            )

        scraper_process = None
        scraper_mode = None
        return False, None


def scraper_is_running():
    running, _ = scraper_status()
    return running


def _finish_graceful_stop(process):
    """Wait for a search to clean up after a stop request; force it only as a fallback."""
    try:
        process.wait(timeout=GRACEFUL_STOP_SECONDS)
    except subprocess.TimeoutExpired:
        log_error_code("E2106", "Job Finder did not stop in time; forcing it.")
        process.kill()
        process.wait(timeout=10)
    finally:
        webfiles.STOP_REQUEST_FILE.unlink(missing_ok=True)


@app.route("/stop-search", methods=["POST"])
def stop_search():
    global scraper_process, scraper_mode, scraper_last_error, scraper_stopping
    with scraper_lock:
        if scraper_process is None or scraper_process.poll() is not None:
            scraper_process = None
            scraper_mode = None
            return jsonify({"status": "stopped", "message": "No Job Finder process was running."})
        if scraper_mode in ("search", "replacement"):
            # Let a search finish its current step and save what it has.
            if not scraper_stopping:
                scraper_stopping = True
                try:
                    webfiles.STOP_REQUEST_FILE.touch()
                except OSError as error:
                    scraper_stopping = False
                    log_error_code("E2105", f"Could not request a stop: {error}")
                    return api_error("E2105", "Could not stop the current Job Finder action.", 500)
                threading.Thread(target=_finish_graceful_stop, args=(scraper_process,), daemon=True).start()
            return jsonify({"status": "stopping"})
        try:
            scraper_process.terminate()
            try:
                scraper_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                scraper_process.kill()
                scraper_process.wait(timeout=5)
            scraper_process = None
            scraper_mode = None
            scraper_last_error = None
            return jsonify({"status": "stopped"})
        except OSError as error:
            return api_error("E2105", "Could not stop the current Job Finder action.", 500)


def validate_search_criteria(job_title, state, cities=None):
    job_title = (job_title or "").strip()[:1000]
    state = (state or "").strip()[:100]

    if not job_title:
        return None, None, None, "Enter a job title."

    if not state:
        return None, None, None, "Enter a state."

    cleaned_cities = []
    allowed_radii = {5, 10, 15, 20, 30, 50}
    if isinstance(cities, list):
        seen = set()
        for item in cities[:25]:
            if not isinstance(item, dict):
                continue
            city = str(item.get("city", "")).strip()[:150]
            try:
                radius = int(item.get("radius", 50))
            except (TypeError, ValueError):
                radius = 50
            if city and radius in allowed_radii and city.lower() not in seen:
                seen.add(city.lower())
                cleaned_cities.append({"city": city, "radius": radius})

    return job_title, state, cleaned_cities, None


def launch_search_process(job_title, state, cities=None, mode="search"):
    global scraper_process, scraper_mode, scraper_started_at

    job_title, state, cities, validation_error = validate_search_criteria(job_title, state, cities)
    if validation_error:
        return jsonify({"status": "error", "message": validation_error}), 400

    with scraper_lock:
        if scraper_process is not None and scraper_process.poll() is None:
            return jsonify({"status": "already_running", "mode": scraper_mode}), 200

        if not JOB_FINDER_PATH.exists():
            return jsonify({"status": "error", "message": "job_finder.py was not found."}), 500

        # Keep Search and User Profile on the same saved job-title/location data,
        # but only once this search is actually going to start.
        if mode != "replacement":
            try:
                profile = profile_store.get_user_profile()
                titles = []
                for part in job_title.split(","):
                    title = part.strip()[:255]
                    if title and title.lower() not in {item.lower() for item in titles}:
                        titles.append(title)
                profile_store.save_user_profile(
                    profile.get("first_name", ""),
                    profile.get("last_name", ""),
                    state,
                    titles,
                    cities,
                )
            except Exception as error:
                log_error_code("E3301", f"Could not sync search criteria to profile: {error}")

        try:
            creationflags = 0
            if os.name == "nt":
                creationflags = subprocess.CREATE_NO_WINDOW

            global scraper_last_error
            scraper_last_error = None
            # A leftover request from an earlier stop would end this search immediately.
            webfiles.STOP_REQUEST_FILE.unlink(missing_ok=True)
            with webfiles.SCRAPER_LOG_FILE.open("a", encoding="utf-8") as log_file:
                log_file.write(
                    f"\n=== {datetime.now().isoformat(timespec='seconds')} | {mode} | "
                    f"{job_title} | {state} ===\n"
                )
                log_file.flush()
                scraper_process = subprocess.Popen(
                    [
                        sys.executable,
                        "-u",
                        str(JOB_FINDER_PATH),
                        "--job-title",
                        job_title,
                        "--state",
                        state,
                        "--cities-json",
                        json.dumps(cities or []),
                    ] + (["--max-new", "1"] if mode == "replacement" else []),
                    cwd=str(BASE_DIR),
                    creationflags=creationflags,
                    stdout=log_file,
                    stderr=subprocess.STDOUT,
                )
            scraper_mode = mode
            scraper_started_at = time.monotonic()
            if mode != "replacement":
                record_search_history(job_title, state, cities)
        except OSError as error:
            scraper_process = None
            scraper_mode = None
            return jsonify({"status": "error", "message": str(error)}), 500

    return jsonify({"status": "started"}), 202


_home_state_cache = {"value": "", "at": 0.0}


def _home_state():
    """The profile's home state ("MD"), re-read at most once a minute so typing doesn't hit the database."""
    if time.monotonic() - _home_state_cache["at"] > 60:
        _home_state_cache.update(value=(profile_store.get_user_profile().get("state") or "")[:2], at=time.monotonic())
    return _home_state_cache["value"]


@app.route("/start-search", methods=["POST"])
def start_search():
    data = request.get_json(silent=True) or {}
    return launch_search_process(
        data.get("job_title"),
        data.get("state"),
        data.get("cities"),
        mode="search",
    )


@app.route("/replace-result", methods=["POST"])
def replace_result():
    history = get_search_history(limit=1)
    if not history:
        return jsonify({"status": "no_results", "message": "Start a search to save criteria before requesting a replacement."}), 200
    search = history[0]
    try:
        cities = json.loads(search.get("cities_json") or "[]")
    except (TypeError, ValueError):
        cities = []
    return launch_search_process(search.get("job_title"), search.get("state"), cities, mode="replacement")


@app.route("/refresh-search", methods=["POST"])
def refresh_search():
    """Recheck the result rows currently shown without changing search criteria."""
    global scraper_process, scraper_mode, scraper_started_at, scraper_last_error
    data = request.get_json(silent=True) or {}
    ids = data.get("company_ids")
    if not isinstance(ids, list) or len(ids) > 500 or any(type(value) is not int or value <= 0 for value in ids):
        return jsonify({"status": "error", "message": "Invalid result selection."}), 400
    ids = list(dict.fromkeys(ids))
    if not ids:
        return jsonify({"status": "no_results", "message": "There are no displayed listings to refresh."})
    with scraper_lock:
        if scraper_process is not None and scraper_process.poll() is None:
            return jsonify({"status": "already_running", "mode": scraper_mode}), 200
        if not JOB_FINDER_PATH.exists():
            return jsonify({"status": "error", "message": "job_finder.py was not found."}), 500
        try:
            flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            scraper_last_error = None
            with webfiles.SCRAPER_LOG_FILE.open("a", encoding="utf-8") as log_file:
                log_file.write(f"\n=== {datetime.now().isoformat(timespec='seconds')} | update-existing ===\n")
                log_file.flush()
                scraper_process = subprocess.Popen(
                    [sys.executable, "-u", str(JOB_FINDER_PATH), "--update-existing", "--update-ids", ",".join(map(str, ids))],
                    cwd=str(BASE_DIR), creationflags=flags, stdout=log_file, stderr=subprocess.STDOUT,
                )
            scraper_mode = "refresh"
            scraper_started_at = time.monotonic()
        except OSError as error:
            scraper_process = None
            scraper_mode = None
            return jsonify({"status": "error", "message": str(error)}), 500
    return jsonify({"status": "started"}), 202


@app.route("/update-existing", methods=["POST"])
def update_existing():
    global scraper_process, scraper_mode, scraper_started_at

    with scraper_lock:
        if scraper_process is not None and scraper_process.poll() is None:
            return jsonify({"status": "already_running", "mode": scraper_mode}), 200

        if not JOB_FINDER_PATH.exists():
            return jsonify({"status": "error", "message": "job_finder.py was not found."}), 500

        try:
            creationflags = 0
            if os.name == "nt":
                creationflags = subprocess.CREATE_NO_WINDOW

            global scraper_last_error
            scraper_last_error = None
            with webfiles.SCRAPER_LOG_FILE.open("a", encoding="utf-8") as log_file:
                log_file.write(
                    f"\n=== {datetime.now().isoformat(timespec='seconds')} | update-existing ===\n"
                )
                log_file.flush()
                scraper_process = subprocess.Popen(
                    [sys.executable, "-u", str(JOB_FINDER_PATH), "--update-existing"],
                    cwd=str(BASE_DIR),
                    creationflags=creationflags,
                    stdout=log_file,
                    stderr=subprocess.STDOUT,
                )
            scraper_mode = "update"
            scraper_started_at = time.monotonic()
        except OSError as error:
            scraper_process = None
            scraper_mode = None
            return jsonify({"status": "error", "message": str(error)}), 500

    return jsonify({"status": "started"}), 202


@app.route("/captures/pending")
def captures_pending():
    """Capture files the Web Job Scraper extension left in Downloads, for the import prompt."""
    downloads_dir, searches_dir = capture_dirs(read_tuning_settings(), BASE_DIR)
    try:
        files = pending_files(downloads_dir)
    except OSError as error:
        return api_error("E2120", f"Could not read {downloads_dir}: {error}", 500)
    return jsonify({
        "count": len(files),
        "jobs": sum(item["jobs"] for item in files),
        "from": str(downloads_dir),
        "to": str(searches_dir),
        # Changes whenever a file is added or rewritten, so "Not now" only hides the prompt until something new arrives.
        "signature": ";".join(f"{item['relative']}@{int(item['modified'])}" for item in files),
    })


@app.route("/captures/import", methods=["POST"])
def captures_import():
    """Move the waiting capture files into web-job-scraper\\searches, then run job_finder.py --import-captures."""
    global scraper_process, scraper_mode, scraper_started_at, scraper_last_error
    downloads_dir, searches_dir = capture_dirs(read_tuning_settings(), BASE_DIR)
    with scraper_lock:
        if scraper_process is not None and scraper_process.poll() is None:
            return jsonify({"status": "already_running", "mode": scraper_mode}), 200
        try:
            moved = move_pending(downloads_dir, searches_dir)
        except OSError as error:
            return api_error("E2121", f"Could not move the capture files to {searches_dir}: {error}", 500)
        try:
            flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            scraper_last_error = None
            with webfiles.SCRAPER_LOG_FILE.open("a", encoding="utf-8") as log_file:
                log_file.write(f"\n=== {datetime.now().isoformat(timespec='seconds')} | import | "
                               f"{len(moved)} capture file(s) ===\n")
                log_file.flush()
                scraper_process = subprocess.Popen(
                    [sys.executable, "-u", str(JOB_FINDER_PATH), "--import-captures"],
                    cwd=str(BASE_DIR), creationflags=flags, stdout=log_file, stderr=subprocess.STDOUT,
                )
            scraper_mode = "import"
            scraper_started_at = time.monotonic()
        except OSError as error:
            scraper_process = None
            scraper_mode = None
            return api_error("E2121", f"Could not start the import: {error}", 500)
    return jsonify({"status": "started", "moved": len(moved)}), 202


def import_progress_from_log():
    if not webfiles.SCRAPER_LOG_FILE.exists():
        return "Preparing the import"
    try:
        with webfiles.SCRAPER_LOG_FILE.open("rb") as log_file:
            size = log_file.seek(0, 2)
            log_file.seek(max(0, size - 65536))
            tail = log_file.read().decode("utf-8", errors="replace")
    except OSError:
        return "Preparing the import"
    current = tail.rsplit("| import |", 1)[-1]
    matches = list(re.finditer(r"Importing (\d+)/(\d+): ([^\r\n]+)", current))
    if matches:
        latest = matches[-1]
        return f"Checking {latest.group(1)} of {latest.group(2)}: {latest.group(3)[:70]}"
    return "Preparing the import"


def update_progress_from_log():
    if not webfiles.SCRAPER_LOG_FILE.exists():
        return "Preparing existing results"
    try:
        with webfiles.SCRAPER_LOG_FILE.open("rb") as log_file:
            size = log_file.seek(0, 2)
            log_file.seek(max(0, size - 65536))
            tail = log_file.read().decode("utf-8", errors="replace")
    except OSError:
        return "Preparing existing results"
    current = tail.rsplit("| update-existing ===", 1)[-1]
    matches = list(re.finditer(r"Updating (\d+)/(\d+): ([^\r\n]+)", current))
    if matches:
        latest = matches[-1]
        completed = "Existing row updated in place." in current[latest.end():] or "Could not update this existing row." in current[latest.end():]
        verb = "Checked" if completed else "Checking"
        return f"{verb} {latest.group(1)} of {latest.group(2)}: {latest.group(3)[:70]}"
    found = re.findall(r"Found (\d+) existing results to verify", current)
    if found:
        return f"Found {found[-1]} existing results to check"
    return "Preparing existing results"


def search_progress_from_log(mode):
    fallback = "Starting replacement search" if mode == "replacement" else "Starting search"
    if not webfiles.SCRAPER_LOG_FILE.exists():
        return {"progress": fallback, "passed": 0, "checked": 0, "saved": 0, "limit": None}
    try:
        with webfiles.SCRAPER_LOG_FILE.open("rb") as log_file:
            size = log_file.seek(0, 2)
            log_file.seek(max(0, size - 2097152))
            tail = log_file.read().decode("utf-8", errors="replace")
    except OSError:
        return {"progress": fallback, "passed": 0, "checked": 0, "saved": 0, "limit": None}
    markers = list(re.finditer(r"\| " + re.escape(mode) + r" \|[^\r\n]*===", tail))
    if not markers:
        return {"progress": fallback, "passed": 0, "checked": 0, "saved": 0, "limit": None}
    current = tail[markers[-1].end():]
    candidates = list(re.finditer(r"Checking result (\d+): ([^\r\n]+)", current))
    passed_matches = list(re.finditer(r"Passed validation: (\d+)", current))
    passed = int(passed_matches[-1].group(1)) if passed_matches else 0
    # "Passed" counts every job that passed, including ones already in the list; only new saves count toward the limit.
    saved_matches = list(re.finditer(r"Saved (?:job |viable company \()(\d+)/(\d+)", current))
    saved = int(saved_matches[-1].group(1)) if saved_matches else 0
    limit = int(saved_matches[-1].group(2)) if saved_matches else None
    events = list(re.finditer(r"^(Checking result \d+: |Skipped \([^\r\n]+?\): |Passed validation: \d+ · |Saved lead for review: )([^\r\n]+)", current, re.M))
    if events:
        latest = events[-1]
        progress = (latest.group(1) + latest.group(2))[:220]
    else:
        progress = "Searching for results"
    checked = int(candidates[-1].group(1)) if candidates else 0
    return {"progress": progress, "passed": passed, "checked": checked, "saved": saved, "limit": limit}


@app.route("/search-status")
def search_status():
    running, mode = scraper_status()
    elapsed = int(time.monotonic() - scraper_started_at) if running and scraper_started_at else 0
    activity = search_progress_from_log(mode) if running and mode in ("search", "replacement") else None
    stop_reason = None
    if not running and webfiles.SCRAPER_LOG_FILE.exists():
        try:
            with webfiles.SCRAPER_LOG_FILE.open("rb") as log_file:
                size = log_file.seek(0, 2)
                log_file.seek(max(0, size - 8192))
                reasons = re.findall(r"Stop reason: ([^\r\n]+)", log_file.read().decode("utf-8", errors="replace"))
                stop_reason = reasons[-1] if reasons else None
        except OSError:
            pass
    return jsonify({
        "running": running,
        "mode": mode,
        "stopping": running and scraper_stopping,
        "error": scraper_last_error,
        "progress": (update_progress_from_log() if mode in ("update", "refresh") else import_progress_from_log() if mode == "import"
                     else activity["progress"] if activity else None) if running else None,
        "passed": activity["passed"] if activity else None,
        "saved": activity["saved"] if activity else None,
        "limit": activity["limit"] if activity else None,
        "checked": activity["checked"] if activity else None,
        "elapsed_seconds": elapsed,
        "stop_reason": stop_reason,
    })
