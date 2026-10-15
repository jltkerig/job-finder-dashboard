"""Local governments near a place you typed (suggested on the Search page), and watching one with a click."""
import json
import os
from pathlib import Path

from flask import jsonify, request

from jobfinder.profiles.travel import place_point, zip_point
from jobfinder.sources import local_government
from jobfinder.sources.employer_jobs import CONFIG_FILE, DEFAULT_EMPLOYERS, config_key, load_employers
from jobfinder.web import profile_store
from jobfinder.web.core import api_error, app


def _point(text, state):
    text = str(text or "").strip()
    if len(text) == 5 and text.isdigit():
        return zip_point(text)
    return place_point(text, state)


def _watched_keys():
    return {config_key({"system": "neogov", "agency": employer.config.get("agency")}) for employer in load_employers()
            if employer.system == "neogov"}


@app.route("/local-governments")
def local_governments():
    """Governments around ?place= (a town and state, or a ZIP) that have a NEOGOV job page, with whether each is watched."""
    text = (request.args.get("place") or "").strip()
    if not text:
        return api_error("E3601", "Type a town and state or a ZIP code first.", 400)
    point = _point(text, str(profile_store.get_user_profile().get("state") or ""))
    if point is None:
        return api_error("E3602", "That place wasn't found. Use a town and state such as Bel Air, MD, or a 5-digit ZIP code.", 404)
    result = local_government.suggestions(point)
    if result is None:
        return api_error("E3603", "The county couldn't be looked up just now. Try again in a moment.", 502)
    watched = _watched_keys()
    for item in result["found"]:
        item["watched"] = config_key({"system": "neogov", "agency": item["agency"]}) in watched
    return jsonify(result)


@app.route("/local-governments/watch", methods=["POST"])
def watch_local_government():
    """Add one NEOGOV government to the employers searched (watched_employers.json). Only what was suggested can be added."""
    data = request.get_json(silent=True) or {}
    agency, name = str(data.get("agency") or "").strip(), str(data.get("name") or "").strip()[:150]
    if not agency or not name or not agency.isalnum():
        return api_error("E3604", "Nothing to watch.", 400)
    if local_government.probe(agency) is None:  # a real NEOGOV agency only
        return api_error("E3605", "That government has no NEOGOV job page.", 400)
    try:
        configs = json.loads(Path(CONFIG_FILE).read_text(encoding="utf-8"))
        if not isinstance(configs, list):
            raise ValueError
    except (OSError, ValueError):
        configs = [dict(config) for config in DEFAULT_EMPLOYERS]  # no file yet: start from the built-in list, not from nothing
    entry = {"name": name, "system": "neogov", "agency": agency}
    if config_key(entry) not in {config_key(item) for item in configs if isinstance(item, dict) and item.get("system") == "neogov"}:
        configs.append(entry)
        temporary = Path(str(CONFIG_FILE) + ".tmp")
        temporary.write_text(json.dumps(configs, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, CONFIG_FILE)
    return jsonify({"ok": True, "name": name})
