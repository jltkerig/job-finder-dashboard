"""What the Web Job Scraper extension asks Job Finder for: updates, your fit profile and distances."""

import hashlib
from pathlib import Path

from flask import abort, jsonify, request, send_from_directory

from jobfinder.profiles.onet_data import spelling_fix
from jobfinder.profiles.profile_tools import AMBIGUOUS_SKILLS, SKILL_ALIASES
from jobfinder.profiles.travel import describe as describe_trip
from jobfinder.web import blocklists
from jobfinder.web import profile_store
from jobfinder.web.core import app
from jobfinder.web.tuning import read_tuning_settings
from jobfinder.web.webfiles import BASE_DIR, EXTENSION_FILE, EXTENSION_ID


def extension_dist_dir():
    return Path(read_tuning_settings().get("extension_dist_dir") or BASE_DIR.parent / "web-job-scraper" / "dist")


def latest_extension_build():
    """(version text, path) of the newest web-job-scraper-vX.Y.Z.xpi in web-job-scraper\\dist, or (None, None)."""
    folder = extension_dist_dir()
    builds = []
    if folder.is_dir():
        for path in folder.iterdir():
            match = EXTENSION_FILE.match(path.name)
            if match and path.is_file():
                builds.append((tuple(int(part) for part in match.groups()), path))
    if not builds:
        return None, None
    version, path = max(builds)
    return ".".join(map(str, version)), path


@app.route("/extension/updates.json")
def extension_updates():
    """Firefox's "Check for Updates" for the Web Job Scraper extension reads this (its manifest's update_url)."""
    version, path = latest_extension_build()
    updates = []
    if version:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        updates.append({"version": version, "update_link": f"{request.host_url}extension/{path.name}",
                        "update_hash": f"sha256:{digest}"})
    return jsonify({"addons": {EXTENSION_ID: {"updates": updates}}})


@app.route("/extension/fit-profile")
def extension_fit_profile():
    """What Web Job Scraper needs to mark LinkedIn jobs that may fit: your titles, skills and work preferences,
    plus Job Finder's skill names and spellings so Job Fit is worked out the same way as on the Dashboard.

    No CORS header on purpose: only the extension (which has permission for this address) can read it; an
    ordinary web page asking for it gets nothing back.
    """
    profile = profile_store.get_user_profile()
    titles = [spelling_fix(title) for title in
              [profile.get("primary_job_title") or ""] + list(profile.get("job_titles") or [])]  # typos fixed, as in searches
    return jsonify({
        "titles": [title for i, title in enumerate(titles) if title and title.casefold() not in
                   {t.casefold() for t in titles[:i]}],
        "skills": profile.get("skills") or [],
        "work_preferences": profile.get("work_preferences") or [],
        "blocked_companies": blocklists.get_blocked_companies(),  # companies you blocked in Job Finder: hidden on LinkedIn too
        "skill_aliases": SKILL_ALIASES,
        "ambiguous_skills": sorted(AMBIGUOUS_SKILLS),
    })


@app.route("/extension/distances")
def extension_distances():
    """Estimated distance and 6 a.m. drive time from the home ZIP to each place (places separated by |), for the
    LinkedIn markers. Same estimate as the Dashboard (travel.py); nothing is looked up online. No CORS header,
    so only the extension can read it."""
    profile = profile_store.get_user_profile()
    home_zip, home_state = profile.get("home_zip") or "", profile.get("state") or ""
    places = [place.strip()[:120] for place in (request.args.get("places") or "").split("|") if place.strip()][:100]
    return jsonify({"home_zip": home_zip,
                    "places": {place: describe_trip(home_zip, None, place, home_state) if home_zip else None
                               for place in places}})


@app.route("/extension/<name>")
def extension_file(name):
    if not EXTENSION_FILE.match(name):
        abort(404)
    return send_from_directory(extension_dist_dir(), name, mimetype="application/x-xpinstall")
