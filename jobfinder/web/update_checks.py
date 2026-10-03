"""Looking for and installing a newer Job Finder from the update ZIPs."""

import os
import subprocess

from flask import jsonify, request
from jobfinder.web.core import api_error, app, log_error_code
from jobfinder.web.webfiles import BASE_DIR, UPDATE_SCRIPT, UPDATE_SEARCH_DIRS, UPDATE_ZIP_PATTERN


def find_latest_update_zip():
    candidates = []

    for folder in UPDATE_SEARCH_DIRS:
        if not folder.exists() or not folder.is_dir():
            continue

        try:
            entries = folder.iterdir()
        except OSError:
            continue

        for path in entries:
            if not path.is_file():
                continue

            match = UPDATE_ZIP_PATTERN.match(path.name)
            if not match:
                continue

            version = tuple(int(part) for part in match.groups())
            candidates.append((version, path))

    if not candidates:
        return None, None

    version, path = max(candidates, key=lambda item: item[0])
    return version, path


@app.route("/check-update")
def check_update():
    try:
        current_version = tuple(int(part) for part in app.config["APP_VERSION"].split("."))
        latest_version, latest_path = find_latest_update_zip()

        if latest_version is None:
            return jsonify({
                "status": "none_found",
                "current_version": app.config["APP_VERSION"],
                "message": "No Job Finder update ZIPs were found in Downloads or the Python folder.",
            })

        latest_text = ".".join(str(part) for part in latest_version)
        return jsonify({
            "status": "update_available" if latest_version > current_version else "current",
            "current_version": app.config["APP_VERSION"],
            "latest_version": latest_text,
            "file_name": latest_path.name,
            "folder": str(latest_path.parent),
            "message": (
                f"Update v{latest_text} is available."
                if latest_version > current_version
                else f"You already have the latest version found: v{latest_text}."
            ),
        })
    except Exception as error:
        log_error_code("E1401", f"Update check failed: {error}")
        return api_error("E1401", "Could not check for the latest Job Finder update.", 500)


@app.route("/install-update", methods=["POST"])
def install_update():
    data = request.get_json(silent=True) or {}
    requested = (data.get("file_name") or "").strip()
    latest_version, latest_path = find_latest_update_zip()
    if latest_path is None:
        return api_error("E1402", "No update ZIP was found.", 404)
    current_version = tuple(int(part) for part in app.config["APP_VERSION"].split("."))
    if latest_version <= current_version:
        return api_error("E1406", "No newer update is available to install.", 409)
    if requested and latest_path.name != requested:
        return jsonify({
            "status": "update_changed",
            "code": "E1403",
            "message": "A newer update ZIP was found. Review the new version before installing.",
            "latest_version": ".".join(map(str, latest_version)),
            "file_name": latest_path.name,
        }), 409
    if not UPDATE_SCRIPT.exists():
        return api_error("E1404", "update.ps1 was not found.", 500)
    try:
        creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        subprocess.Popen(
            ["powershell.exe", "-ExecutionPolicy", "Bypass", "-File", str(UPDATE_SCRIPT), "-ZipPath", str(latest_path), "-CurrentPid", str(os.getpid())],
            cwd=str(BASE_DIR),
            creationflags=creationflags,
        )
        return jsonify({"status": "updating", "version": ".".join(map(str, latest_version)), "file_name": latest_path.name})
    except OSError as error:
        log_error_code("E1405", f"Could not launch updater: {error}")
        return api_error("E1405", "Could not start the update installer.", 500)


@app.route("/app-version")
def app_version():
    return jsonify({"version": app.config["APP_VERSION"]})
