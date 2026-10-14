"""Reading saved companies and jobs from the database for the Search, Dashboard and Settings pages."""

import json
import math
import re
from urllib.parse import urlparse

from mysql.connector import Error

from jobfinder import db
from jobfinder.profiles.profile_tools import fit_score
from jobfinder.profiles.travel import describe as describe_trip
from jobfinder.records.capture_import import CAPTURE_SOURCES
from jobfinder.search.company_check import is_aggregator, is_ats_host
from jobfinder.sources.job_feeds import FEED_NAMES
from jobfinder.sources.job_listings import NON_JOB_PATH
from jobfinder.sources.job_sites import JOB_SITE_NAMES
from jobfinder.web import blocklists
from jobfinder.web import schema


def get_known_employers(lat, lon, miles, limit=400):
    """Employers Job Finder has seen hiring within miles of a point, nearest first, as the Companies page lists them.

    One entry per company name and town, from the jobs found in earlier searches that have a map point. No network needed."""
    from jobfinder.profiles.travel import miles_between
    degrees = float(miles) / 69.0 + 0.02
    cos = max(math.cos(math.radians(float(lat))), 0.2)
    found = {}
    connection = cursor = None
    try:
        connection = db.connect()
        cursor = connection.cursor(dictionary=True)
        cursor.execute("""
            SELECT id, name, domain, city, state, latitude, longitude, career_job_title
            FROM companies
            WHERE is_rejected = 0 AND name IS NOT NULL AND name <> '' AND latitude IS NOT NULL AND longitude IS NOT NULL
              AND latitude BETWEEN %s AND %s AND longitude BETWEEN %s AND %s
            ORDER BY date_found DESC
        """, (float(lat) - degrees, float(lat) + degrees, float(lon) - degrees / cos, float(lon) + degrees / cos))
        rows = cursor.fetchall()
    except Error as error:
        print("Could not read the known employers.")
        print(error)
        return []
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None and connection.is_connected():
            connection.close()
    for row in rows:
        away = miles_between((float(lat), float(lon)), (float(row["latitude"]), float(row["longitude"])))
        if away > float(miles):
            continue
        key = (str(row["name"]).casefold(), str(row.get("city") or "").casefold())
        if key in found:
            continue
        host = str(row.get("domain") or "").lower().removeprefix("www.")
        own_site = bool(host) and "." in host and not (is_aggregator(host) or is_ats_host(host)) and host != PLACEHOLDER_DOMAIN
        found[key] = {
            "id": f"known/{row['id']}", "name": row["name"], "type": ("Hiring: " + row["career_job_title"])[:60]
            if row.get("career_job_title") else "Employer", "address": ", ".join(p for p in (row.get("city"), row.get("state")) if p),
            "website": f"https://{host}" if own_site else "", "phone": "",
            "lat": round(float(row["latitude"]), 6), "lon": round(float(row["longitude"]), 6), "miles": round(away, 1)}
    return sorted(found.values(), key=lambda company: (company["miles"], company["name"].casefold()))[:limit]


PLACEHOLDER_DOMAIN = "boards.example"  # older searches saved this for boards set up by a slug (Ashby, Greenhouse, Lever)


def fix_placeholder_domains(rows):
    """Rows saved with the placeholder domain show the host their posting is on instead (jobs.ashbyhq.com); the next
    search that finds the job saves it that way too. Rows on a job site's or a hiring system's address are flagged
    domain_is_job_site, so the page offers no Block Domain for them (it would block everyone on that address)."""
    for row in rows:
        if str(row.get("domain") or "").lower() == PLACEHOLDER_DOMAIN:
            url = row.get("career_url") or row.get("source_url") or ""
            row["domain"] = (urlparse(url).hostname or "").removeprefix("www.") or None
        host = str(row.get("domain") or "").lower()
        row["domain_is_job_site"] = bool(host) and (is_aggregator(host) or is_ats_host(host))
    return rows


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
        if status_filter == "__applied":
            where.append("application_status IN ('Applied', 'Talking With Recruiter', 'Interview')")
        elif status_filter == "__not_applied":
            where.append("COALESCE(application_status, 'None') NOT IN ('Applied', 'Talking With Recruiter', 'Interview')")
        elif status_filter:
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
                   is_rejected, rejected_at, listing_details
            FROM companies
            WHERE {' AND '.join(where)}
            ORDER BY {order_by}
        """
        cursor.execute(sql, params)
        return fix_placeholder_domains(cursor.fetchall())
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


def sort_by_job_fit(companies):
    """Highest Job Fit first; jobs with no Job Fit (no skills known) last. Equal fits keep their existing order."""
    companies.sort(key=lambda company: (company["job_fit"]["score"] is None, -(company["job_fit"]["score"] or 0)))
    return companies


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

        rows = fix_placeholder_domains(cursor.fetchall())
        blocked_domains = set(blocklists.get_blocked_domains())
        blocked_names = {name.casefold() for name in blocklists.get_blocked_companies()}
        rows = hide_closed(rows)
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
            # A job site's own address (linkedin.com) is never blocked as a domain: its company is.
            domain = row.get("domain") or ""
            site = urlparse(domain if "://" in domain else "//" + domain).hostname or ""
            row["domain_is_job_site"] = bool(site) and (is_aggregator(site) or is_ats_host(site))
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


APPLIED_STATUSES = ("Applied", "Talking With Recruiter", "Interview")


def hide_queued(companies, everything):
    """The Search page's results without the jobs you added to the Apply Queue, or their copies on other boards: once
    added, a job is worked on from the Dashboard. everything is the list before merge_duplicates, so a queued copy that
    was merged away still counts. A job taken off the queue shows here again."""
    def queued(company):
        details = company.get("details")
        return isinstance(details, dict) and bool(details.get("apply_queued"))

    queued_keys = {_duplicate_key(company) for company in everything if queued(company)} - {None}
    return [company for company in companies
            if not queued(company) and _duplicate_key(company) not in queued_keys]


def hide_closed(rows):
    """A job found to be closed leaves the Search results, saved or not (a saved one stays on the Dashboard, marked
    Closed; an unsaved one is deleted after CLOSED_KEEP_DAYS). Refresh checks each listing again and a reopened one
    comes back."""
    return [row for row in rows if row.get("job_open_status") != "Closed"]


def hide_applied(companies, everything):
    """The Search page's results without the jobs you have applied to, or their copies on other boards: those live on
    the Dashboard. everything is the list before merge_duplicates, so an applied copy that was merged away still counts."""
    applied_keys = {_duplicate_key(company) for company in everything
                    if company.get("application_status") in APPLIED_STATUSES} - {None}
    return [company for company in companies
            if company.get("application_status") not in APPLIED_STATUSES
            and _duplicate_key(company) not in applied_keys]


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
        # A job you saved or applied to always stays; its copies are the ones that are hidden.
        group.sort(key=lambda item: (bool(item.get("is_kept")), item.get("application_status") in APPLIED_STATUSES,
                                     item.get("source_type") == "Employer careers", item.get("career_credibility") or 0,
                                     str(item.get("date_found") or "")), reverse=True)
        keep = group[0]
        keep["also_listed"] = [{"id": other["id"], "source": other.get("source_type") or "Web",
                                "url": other.get("source_url") or other.get("career_url")} for other in group[1:]]
        hidden.update(id(other) for other in group[1:])
    return [company for company in companies if id(company) not in hidden]
