"""Street-address suggestions for the Profile's work history: Job Finder's own lookup (OpenStreetMap through Photon),
nearest the town typed in the same job's City/State/ZIP boxes, else nearest the home town."""
import re
import sys

import config
import jobfinder_db


def _point(place, state=""):
    from jobfinder.profiles.travel import place_point, zip_point
    place = place.strip()
    if re.fullmatch(r"\d{5}(?:-\d{4})?", place):
        return zip_point(place)
    return place_point(place, state) if place else None


def suggest(typed, place=""):
    """Suggestions (street, city, state, zip, address, name, detail) for `typed`; [] when none. ValueError if the service is down."""
    if str(config.JOB_FINDER_DIR) not in sys.path:
        sys.path.append(str(config.JOB_FINDER_DIR))
    try:
        from jobfinder.sources import address_lookup
    except ImportError:
        return []
    near = None
    try:
        profile = jobfinder_db.get_profile()
        for text in (place, profile.get("home_location"), profile.get("home_zip")):
            if text and str(text).strip():
                near = _point(str(text), str(profile.get("state") or ""))
                if near:
                    break
    except Exception:  # no profile or no place: suggestions just aren't biased to a town
        near = None
    return address_lookup.suggest(typed, near)
