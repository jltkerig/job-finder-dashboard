"""Reading saved companies and jobs from the database for the Search, Dashboard and Settings pages."""

from urllib.parse import urlparse
import json
import re

from jobfinder import db
from jobfinder.profiles.profile_tools import fit_score
from jobfinder.profiles.travel import describe as describe_trip
from jobfinder.records.capture_import import CAPTURE_SOURCES
from jobfinder.records.job_retention import SAVED_STATUSES
from jobfinder.sources.job_listings import NON_JOB_PATH
from jobfinder.web import blocklists
from jobfinder.web import schema
from mysql.connector import Error


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
                # The blocked-domain list is for web-search results; jobs you captured on those sites still show.
                and (row.get("source_type") in CAPTURE_SOURCES
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
