"""Reading saved companies and jobs from the database for the Search, Dashboard and Settings pages."""

import json
import re
from urllib.parse import urlparse

from mysql.connector import Error

from jobfinder import db
from jobfinder.profiles.profile_tools import fit_score
from jobfinder.profiles.travel import describe as describe_trip
from jobfinder.records.capture_import import CAPTURE_SOURCES
from jobfinder.records.job_retention import SAVED_STATUSES
from jobfinder.sources.job_feeds import FEED_NAMES
from jobfinder.sources.job_listings import NON_JOB_PATH
from jobfinder.sources.job_sites import JOB_SITE_NAMES
from jobfinder.web import blocklists
from jobfinder.web import schema


def get_dashboard_counts():
    schema.ensure_job_tracking_columns()
    connection = None
    cursor = None
    counts = {"saved": 0, "applied": 0, "recruiter": 0, "interview": 0, "closed": 0}
    try:
        connection = db.connect()
        cursor = connection.cursor(dictionary=True)
        cursor.execute("""
            SELECT
                COUNT(*) AS saved,
                SUM(application_status = 'Applied') AS applied,
                SUM(application_status = 'Talking With Recruiter') AS recruiter,
                SUM(application_status = 'Interview') AS interview,
                SUM(job_open_status = 'Closed') AS closed
            FROM companies
            WHERE is_kept = 1 AND is_rejected = 0
        """)
        row = cursor.fetchone() or {}
        for key in counts:
            counts[key] = int(row.get(key) or 0)
        return counts
    except Error as error:
        print("Could not read dashboard counts.")
        print(error)
        return counts
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None and connection.is_connected():
            connection.close()


def get_rejected_companies():
    schema.ensure_job_tracking_columns()
    connection = None
    cursor = None
    try:
        connection = db.connect()
        cursor = connection.cursor(dictionary=True)
        cursor.execute("""
            SELECT id, name, career_job_title, career_credibility, domain, career_url,
                   source_url, source_type, country, state, city, latitude, longitude, distance_miles, usa_credibility, work_arrangement, date_found,
                   last_checked, result_updated_at, rejected_at, rejection_reason
            FROM companies
            WHERE is_rejected = 1
            ORDER BY rejected_at DESC, date_found DESC
        """)
        return cursor.fetchall()
    except Error as error:
        print("Could not read rejected listings.")
        print(error)
        return []
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None and connection.is_connected():
            connection.close()


def get_kept_companies(status_filter="", state_filter="", title_filter="", sort_by="date_desc"):
    schema.ensure_job_tracking_columns()
    connection = None
    cursor = None

    try:
        connection = db.connect()
        cursor = connection.cursor(dictionary=True)

        where = ["is_kept = 1", "is_rejected = 0"]
        params = []
        if status_filter:
            where.append("application_status = %s")
            params.append(status_filter)
        if state_filter:
            where.append("state LIKE %s")
            params.append(f"%{state_filter}%")
        if title_filter:
            where.append("career_job_title LIKE %s")
            params.append(f"%{title_filter}%")

        sort_map = {
            "date_asc": "date_found ASC",
            "company": "name ASC",
            "title": "career_job_title ASC",
            "status": "application_status ASC, date_found DESC",
            "verified": "last_checked DESC",
        }
        order_by = sort_map.get(sort_by, "date_found DESC")

        sql = f"""
            SELECT id, name, career_job_title, career_credibility, domain, career_url,
                   source_url, source_type, country, state, city, latitude, longitude, distance_miles, usa_credibility, work_arrangement, date_found,
                   last_checked, result_updated_at, is_kept, job_open_status, application_status, notes, listing_skills,
                   is_rejected, rejected_at
            FROM companies
            WHERE {' AND '.join(where)}
            ORDER BY {order_by}
        """
        cursor.execute(sql, params)
        return cursor.fetchall()
    except Error as error:
        print()
        print("Could not read kept companies from the database.")
        print(error)
        return []
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None and connection.is_connected():
            connection.close()


def add_drive_times(companies, home_zip, home_state=""):
    """company["drive"] = {"miles", "minutes", "text"} from the home ZIP, estimated on this computer (travel.py)."""
    for company in companies:
        point = None
        try:
            if company.get("latitude") is not None and company.get("longitude") is not None:
                point = (float(company["latitude"]), float(company["longitude"]))
        except (TypeError, ValueError):
            point = None
        place = ", ".join(part for part in (company.get("city"), company.get("state")) if part)
        company["drive"] = describe_trip(home_zip, point, place, home_state) if home_zip else None


def add_job_fit(companies, skills):
    for company in companies:
        try:
            found = json.loads(company.get("listing_skills") or "[]")
        except (TypeError, ValueError):
            found = []
        company["job_fit"] = fit_score(skills, found if isinstance(found, list) else [])


def get_companies():
    connection = None
    cursor = None

    try:
        connection = db.connect()

        cursor = connection.cursor(dictionary=True)

        cursor.execute("""
            SELECT
                id,
                name,
                career_job_title,
                career_credibility,
                domain,
                career_url,
                source_url,
                country,
                state,
                city,
                latitude,
                longitude,
                distance_miles,
                usa_credibility,
                work_arrangement,
                date_found,
                last_checked,
                result_updated_at,
                is_kept,
                job_open_status,
                application_status,
                notes,
                listing_skills,
                listing_details,
                source_type
            FROM companies
            WHERE is_rejected = 0
            ORDER BY date_found DESC
        """)

        rows = cursor.fetchall()
        blocked_domains = set(blocklists.get_blocked_domains())
        blocked_names = {name.casefold() for name in blocklists.get_blocked_companies()}
        # A job found to be closed disappears from the results (and is deleted after CLOSED_KEEP_DAYS); saved ones stay.
        rows = [row for row in rows if not (row.get("job_open_status") == "Closed" and not row.get("is_kept")
                                           and row.get("application_status") not in SAVED_STATUSES)]
        visible_rows = [row for row in rows if (row.get("name") or "").casefold() not in blocked_names
                and (row.get("is_kept") or not any(
                    NON_JOB_PATH.search(urlparse(row.get(key) or "").path)
                    or (urlparse(row.get(key) or "").hostname or "").startswith("catalystmag.")
                    for key in ("source_url", "career_url")))
                and not (re.search(r"/types-of-aid/employment/campus/?(?:[?#]|$)",
                                   (row.get("career_url") or "").lower())
                         and (row.get("career_job_title") or "").strip().casefold() in
                         {"directory search", "campus employment & internships", "student employment"})
                # The blocked-domain list is for web-search results: jobs you captured on those sites, and jobs from the
                # remote feeds and job sites (whose domain is the board's, such as remoteok.com), still show.
                and (row.get("source_type") in CAPTURE_SOURCES or row.get("source_type") in FEED_NAMES
                     or row.get("source_type") in JOB_SITE_NAMES
                     or not any((row.get("domain") or "").lower().removeprefix("www.") == domain
                                or (row.get("domain") or "").lower().endswith("." + domain)
                                for domain in blocked_domains))]
        for row in visible_rows:
            try:
                row["details"] = json.loads(row.get("listing_details") or "{}")
            except (TypeError, ValueError):
                row["details"] = {}
            if not isinstance(row["details"], dict):
                row["details"] = {}
            row["source_host"] = (urlparse(row.get("source_url") or "").hostname or "").removeprefix("www.")
        return visible_rows

    except Error as error:
        print()
        print("Could not read companies from the database.")
        print(error)
        return []

    finally:
        if cursor is not None:
            cursor.close()

        if connection is not None and connection.is_connected():
            connection.close()


def add_current_distances(companies, cities):
    """Distance from the nearest of your current search cities, worked out on each load: the distance saved with a
    listing is from whichever cities that search used, so a DC listing found by an earlier DC search showed "0 miles"."""
    from jobfinder.search.geo import geocode_location, haversine_miles

    connection = None
    points = []
    try:
        connection = db.connect()
        for item in cities or []:
            place = geocode_location(connection, item.get("city") if isinstance(item, dict) else str(item))
            if place:
                points.append((place["lat"], place["lon"]))
    except Exception:  # no database or geocoder: keep the saved distances
        return
    finally:
        if connection:
            connection.close()
    if not points:
        return
    for company in companies:
        try:
            lat, lon = float(company["latitude"]), float(company["longitude"])
        except (KeyError, TypeError, ValueError):
            continue
        if company.get("work_arrangement") == "Remote":
            continue
        company["distance_miles"] = round(min(haversine_miles(lat, lon, *point) for point in points), 2)


_COMPANY_FILLER = {"the", "inc", "llc", "ltd", "co", "corp", "company", "group", "government", "gov", "of"}


def _duplicate_key(company):
    """(company, title) for spotting one job found on several job boards: the company's first real word, since boards
    shorten names ("Sinclair" / "Sinclair Broadcast Group", "T Rowe Price" / "T. Rowe Price"), and the title with
    punctuation, "[Remote]" tags and the like removed."""
    words = [word for word in re.findall(r"[a-z0-9]+", str(company.get("name") or "").casefold())
             if word not in _COMPANY_FILLER]
    name = "".join(words[:2]) if words and len(words[0]) <= 2 else (words[0] if words else "")
    title = re.sub(r"\[[^\]]*\]|\((?:remote|hybrid|on-?site)\)", " ", str(company.get("career_job_title") or "").casefold())
    title = " ".join(re.findall(r"[a-z0-9]+", title))
    return (name, title) if name and title else None


def merge_duplicates(companies):
    """One row per job: when the same job was found on several boards, keep the best source (employer careers site,
    then the higher credibility, then the newest) and list the others on it as "also_listed". Rejected listings are
    left alone."""
    groups = {}
    for company in companies:
        key = _duplicate_key(company)
        if key and not company.get("is_rejected"):
            groups.setdefault(key, []).append(company)
    hidden = set()
    for group in groups.values():
        if len(group) < 2:
            continue
        group.sort(key=lambda item: (item.get("source_type") == "Employer careers", item.get("career_credibility") or 0,
                                     str(item.get("date_found") or "")), reverse=True)
        keep = group[0]
        keep["also_listed"] = [{"id": other["id"], "source": other.get("source_type") or "Web",
                                "url": other.get("source_url") or other.get("career_url")} for other in group[1:]]
        hidden.update(id(other) for other in group[1:])
    return [company for company in companies if id(company) not in hidden]
