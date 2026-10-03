"""The Flask app and what every request goes through: security checks, error codes, compression and the
date filters.
"""

from datetime import datetime, timezone
import gzip
import os
import secrets

from dotenv import load_dotenv
from flask import Flask, abort, jsonify, request, session

from jobfinder import paths
from jobfinder.records.capture_import import CAPTURE_SOURCES
from jobfinder.sources.job_feeds import FEED_NAMES
from jobfinder.sources.job_sites import JOB_SITE_NAMES
from jobfinder.web.webfiles import APPLICATION_STATUSES, COMPRESSIBLE, LOCAL_HOSTS, LOCAL_TIMEZONE

load_dotenv(paths.ENV_FILE)  # the database login and the secret key live in .env; read before anything uses them

app = Flask(__name__, template_folder=str(paths.ROOT / "templates"), static_folder=str(paths.ROOT / "static"))
app.config["SECRET_KEY"] = os.getenv("FLASK_SECRET_KEY") or secrets.token_hex(32)
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Strict"

ERROR_CODES = {
    "csrf": "E2201",
    "bad_request": "E2001",
    "not_found": "E2002",
    "database": "E3001",
    "process": "E2003",
    "unexpected": "E9001",
}


def log_error_code(code, message):
    print(f"[{code}] {message}")


def api_error(code, message, status=400):
    log_error_code(code, message)
    return jsonify({"status": "error", "error_code": code, "message": message}), status


@app.errorhandler(403)
def handle_forbidden(error):
    if request.path.startswith(("/start-search", "/replace-result", "/refresh-search", "/update-existing", "/save-kept", "/search-status", "/reject-listing", "/restore-rejected", "/block-domain", "/unsave-kept", "/stop-search", "/install-update", "/captures/")):
        return api_error("E2201", "Your dashboard session expired. The page will refresh automatically.", 403)
    return error


@app.errorhandler(500)
def handle_internal_error(error):
    log_error_code("E9001", f"Unhandled server error on {request.path}: {error}")
    if request.path.startswith(("/start-search", "/replace-result", "/refresh-search", "/update-existing", "/save-kept", "/search-status", "/reject-listing", "/restore-rejected", "/block-domain", "/unsave-kept", "/stop-search", "/install-update", "/job-title-suggestions")):
        return jsonify({"status": "error", "error_code": "E9001", "message": "The dashboard hit an unexpected server error."}), 500
    return "Internal server error [E9001]", 500


API_PATH_CODES = {
    "/start-search": "E2101",
    "/replace-result": "E2110",
    "/refresh-search": "E2102",
    "/update-existing": "E2103",
    "/save-kept": "E3101",
    "/search-status": "E2104",
    "/job-title-suggestions": "E1101",
    "/unsave-kept": "E3102",
    "/stop-search": "E2105",
    "/captures/pending": "E2120",
    "/captures/import": "E2121",
}


@app.after_request
def attach_error_codes(response):
    if response.status_code < 400:
        return response

    code = API_PATH_CODES.get(request.path)
    if not code or not response.is_json:
        return response

    payload = response.get_json(silent=True) or {}
    if "error_code" not in payload:
        payload["error_code"] = code
        response.set_data(app.json.dumps(payload))
        response.mimetype = "application/json"
        log_error_code(code, f"{request.method} {request.path} -> HTTP {response.status_code}: {payload.get('message', 'Request failed')}")
    return response


def get_csrf_token():
    token = session.get("csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["csrf_token"] = token
    return token


@app.context_processor
def inject_csrf_token():
    # Job sites (National Labor Exchange) list many employers' jobs like the remote feeds: shown as job-board
    # postings, and their domain is never offered for blocking.
    return {"csrf_token": get_csrf_token(), "app_version": app.config["APP_VERSION"], "feed_names": sorted(FEED_NAMES | JOB_SITE_NAMES),
            "capture_sources": sorted(CAPTURE_SOURCES), "application_statuses": APPLICATION_STATUSES}


@app.before_request
def refuse_foreign_hosts():
    """Answer only requests addressed to this computer. A web page can point its own name at 127.0.0.1 ("DNS
    rebinding") and then read the profile and jobs from here; such a request carries that page's name as the Host."""
    host = (request.host or "").rsplit(":", 1)[0] if not request.host.startswith("[") else request.host.split("]")[0] + "]"
    if host.lower() not in LOCAL_HOSTS:
        abort(400)
    return None


@app.after_request
def make_responses_lighter(response):
    """Send pages, styles, scripts and JSON gzipped (about a fifth of the size), and let the browser keep versioned
    static files (the page links them with ?v=<version>) so they are not downloaded again on every visit."""
    if request.path.startswith("/static/") and request.args.get("v"):
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    wants_gzip = "gzip" in request.headers.get("Accept-Encoding", "")
    if (response.status_code == 200 and wants_gzip and response.mimetype in COMPRESSIBLE
            and "Content-Encoding" not in response.headers and (response.direct_passthrough or not response.is_streamed)):
        response.direct_passthrough = False
        data = response.get_data()
        if len(data) > 1024:
            response.set_data(gzip.compress(data, compresslevel=6))
            response.headers["Content-Encoding"] = "gzip"
            response.headers["Content-Length"] = str(len(response.get_data()))
        response.headers.add("Vary", "Accept-Encoding")
    return response


@app.before_request
def protect_local_post_requests():
    if request.method != "POST":
        return None

    supplied = request.headers.get("X-CSRF-Token") or request.form.get("csrf_token")
    expected = session.get("csrf_token")
    if not supplied or not expected or not secrets.compare_digest(supplied, expected):
        abort(403)

    return None


@app.template_filter("local_time")
def local_time(value, fmt="%b %d, %Y %I:%M %p"):
    """Found, last-checked and updated times are saved in UTC; show them on this computer's clock."""
    if not value:
        return ""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(LOCAL_TIMEZONE).strftime(fmt)


@app.template_filter("how_long")
def how_long(value, now=None):
    """"3 days", "5 hours", "20 minutes": how long ago a saved (UTC) time was."""
    if not value:
        return ""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    seconds = max(0, int(((now or datetime.now(timezone.utc)) - value).total_seconds()))
    for unit, size in (("week", 604800), ("day", 86400), ("hour", 3600), ("minute", 60)):
        if seconds >= size:
            count = seconds // size
            return f"{count} {unit}{'' if count == 1 else 's'}"
    return "less than a minute"
