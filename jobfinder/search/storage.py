"""The database side of the search: connecting, making sure the tables exist, and saving a company or posting."""

from datetime import datetime, timezone
import json
import os

from mysql.connector import Error

from jobfinder import db
from jobfinder.db_schema import ensure_unique_source_index
from jobfinder.sources.job_listings import canonical_url


DB_HOST = os.getenv("DB_HOST", "127.0.0.1")


DB_PORT = int(os.getenv("DB_PORT", "3306"))


DB_USER = os.getenv("DB_USER", "root")


DB_PASSWORD = os.getenv("DB_PASSWORD", "")


DB_NAME = os.getenv("DB_NAME", "job_finder")


def connect_database():
    try:
        return db.connect()

    except Error as error:
        print()
        print("Could not connect " "to the database.")

        print(error)

        return None


def ensure_database_schema(connection):
    cursor = None

    try:
        cursor = connection.cursor()
        for legacy, current in (("career_confidence", "career_credibility"),
                                ("usa_confidence", "usa_credibility")):
            cursor.execute("SHOW COLUMNS FROM companies LIKE %s", (legacy,))
            old_exists = cursor.fetchone() is not None
            cursor.execute("SHOW COLUMNS FROM companies LIKE %s", (current,))
            new_exists = cursor.fetchone() is not None
            if old_exists and not new_exists:
                cursor.execute(f"ALTER TABLE companies CHANGE COLUMN {legacy} {current} INT NULL")
            elif old_exists and new_exists:
                cursor.execute(f"UPDATE companies SET {current} = COALESCE({current}, {legacy})")
                cursor.execute(f"ALTER TABLE companies DROP COLUMN {legacy}")

        required_columns = {
            "career_job_title": "VARCHAR(255) NULL AFTER name",
            "career_credibility": "INT NULL AFTER career_job_title",
            "job_open_status": "VARCHAR(20) NOT NULL DEFAULT 'Open' AFTER career_credibility",
            "application_status": "VARCHAR(30) NOT NULL DEFAULT 'None' AFTER job_open_status",
            "notes": "TEXT NULL AFTER application_status",
            "source_type": "VARCHAR(50) NOT NULL DEFAULT 'SearXNG' AFTER source_url",
            "city": "VARCHAR(150) NULL AFTER state",
            "latitude": "DECIMAL(10,7) NULL AFTER city",
            "longitude": "DECIMAL(10,7) NULL AFTER latitude",
            "distance_miles": "DECIMAL(8,2) NULL AFTER longitude",
            "result_updated_at": "TIMESTAMP NULL DEFAULT NULL AFTER last_checked",
            "work_arrangement": "VARCHAR(20) NULL AFTER distance_miles",
            "listing_skills": "TEXT NULL AFTER work_arrangement",
            "listing_details": "TEXT NULL AFTER listing_skills",
            "is_rejected": "TINYINT(1) NOT NULL DEFAULT 0 AFTER is_kept",
            "rejection_reason": "VARCHAR(40) NULL AFTER is_rejected",
            "rejected_at": "TIMESTAMP NULL DEFAULT NULL AFTER rejection_reason",
            "rejected_by": "VARCHAR(10) NULL AFTER rejected_at",
            "pre_reject_kept": "TINYINT(1) NULL AFTER rejected_at",
            "pre_reject_status": "VARCHAR(30) NULL AFTER pre_reject_kept",
        }

        for column_name, definition in required_columns.items():
            cursor.execute(
                "SHOW COLUMNS FROM companies LIKE %s",
                (column_name,),
            )

            if cursor.fetchone() is None:
                cursor.execute(
                    f"ALTER TABLE companies ADD COLUMN {column_name} {definition}"
                )

        index_status = ensure_unique_source_index(cursor)
        if index_status:
            print(f"Database index update: {index_status}")

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS geocode_cache (
                query_text VARCHAR(255) PRIMARY KEY,
                latitude DECIMAL(10,7) NOT NULL,
                longitude DECIMAL(10,7) NOT NULL,
                display_name TEXT NULL,
                updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
            )
        """)
        connection.commit()
        return True

    except Error as error:
        print()
        print("Could not update database schema.")
        print(error)
        return False

    finally:
        if cursor is not None:
            cursor.close()


def clear_companies_table(connection):
    cursor = None

    try:
        cursor = connection.cursor()

        cursor.execute("TRUNCATE TABLE companies")

        connection.commit()

        print()
        print("Previous test company data cleared.")

        return True

    except Error as error:
        print()
        print("Could not clear companies table.")
        print(error)

        return False

    finally:
        if cursor is not None:
            cursor.close()


def save_company(
    connection,
    name,
    career_job_title,
    career_credibility,
    domain,
    career_url,
    source_url,
    country,
    state,
    usa_credibility,
    city=None,
    latitude=None,
    longitude=None,
    distance_miles=None,
    work_arrangement=None,
    skills=None,
    source_type="SearXNG",
    listing_details=None,
):
    now = datetime.now(timezone.utc)
    cursor = None

    try:
        cursor = connection.cursor()

        # An employer can have many openings; the individual posting URL is the key.
        cursor.execute("SELECT id FROM companies WHERE source_url = %s LIMIT 1", (source_url,))
        duplicate = cursor.fetchone()
        if duplicate:
            cursor.execute(
                """
                UPDATE companies
                SET name = %s, domain = %s, career_job_title = %s, career_credibility = %s, career_url = %s, source_url = %s,
                    source_type = %s, country = %s, state = %s, city = %s, latitude = %s, longitude = %s, distance_miles = %s, usa_credibility = %s, work_arrangement = %s,
                    listing_skills = %s, listing_details = %s, job_open_status = 'Open', last_checked = %s,
                    result_updated_at = %s
                WHERE id = %s
                """,
                (name, domain, career_job_title, career_credibility, career_url, source_url, source_type, country, state, city, latitude, longitude, distance_miles, usa_credibility, work_arrangement, json.dumps(skills or []), json.dumps(listing_details or {}), now, now, duplicate[0]),
            )
            connection.commit()
            return False

        sql = """
        INSERT INTO companies
        (
            name,
            career_job_title,
            career_credibility,
            domain,
            career_url,
            source_url,
            source_type,
            job_open_status,
            country,
            state,
            city,
            latitude,
            longitude,
            distance_miles,
            usa_credibility,
            work_arrangement,
            listing_skills,
            listing_details,
            date_found,
            last_checked
        )
        VALUES
        (
            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
        )
        ON DUPLICATE KEY UPDATE
            name = VALUES(name),
            career_job_title = VALUES(career_job_title),
            career_credibility = VALUES(career_credibility),
            career_url = VALUES(career_url),
            source_url = VALUES(source_url),
            source_type = VALUES(source_type),
            job_open_status = VALUES(job_open_status),
            country = VALUES(country),
            state = VALUES(state),
            city = VALUES(city),
            latitude = VALUES(latitude),
            longitude = VALUES(longitude),
            distance_miles = VALUES(distance_miles),
            usa_credibility = VALUES(usa_credibility),
            work_arrangement = VALUES(work_arrangement),
            listing_skills = VALUES(listing_skills),
            listing_details = VALUES(listing_details),
            last_checked = VALUES(last_checked)
        """

        cursor.execute(
            sql,
            (
                name,
                career_job_title,
                career_credibility,
                domain,
                career_url,
                source_url,
                source_type,
                "Open",
                country,
                state,
                city,
                latitude,
                longitude,
                distance_miles,
                usa_credibility,
                work_arrangement,
                json.dumps(skills or []),
                json.dumps(listing_details or {}),
                now,
                now,
            ),
        )

        connection.commit()
        return cursor.rowcount == 1

    except Error as error:
        print(f"Could not save {domain}.")
        print(error)
        return False

    finally:
        if cursor is not None:
            cursor.close()


def rejected_posting_urls(connection):
    """Do not rediscover openings the user explicitly rejected."""
    cursor = None
    try:
        cursor = connection.cursor()
        cursor.execute("SELECT source_url FROM companies WHERE is_rejected = 1 AND source_url IS NOT NULL")
        return {canonical_url(row[0]) for row in cursor.fetchall() if row[0]}
    except Error:
        return set()
    finally:
        if cursor is not None:
            cursor.close()
