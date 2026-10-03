"""Making sure the database has every table and column the pages need (run once at start, and before
the first save).
"""

import re

from mysql.connector import Error

from jobfinder import db
from jobfinder.db_schema import ensure_unique_source_index
from jobfinder.records.job_retention import CLOSED_KEEP_DAYS, tidy_closed_jobs

# Schema checks that already succeeded in this process; they only need to run once.
_schema_ready = set()


def _safe_db_identifier(value):
    if not re.fullmatch(r"[A-Za-z0-9_]+", value or ""):
        raise ValueError("DB_NAME may contain only letters, numbers, and underscores.")
    return value


def initialize_database():
    """Create only missing database objects. Never drops or overwrites existing data."""
    database_name = _safe_db_identifier(db.settings()["database"])
    connection = None
    cursor = None
    try:
        connection = db.connect(database=False)
        cursor = connection.cursor()
        cursor.execute(f"CREATE DATABASE IF NOT EXISTS `{database_name}`")
        cursor.execute(f"USE `{database_name}`")
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS companies (
                id INT AUTO_INCREMENT PRIMARY KEY,
                name VARCHAR(255) NULL,
                career_job_title VARCHAR(255) NULL,
                career_credibility INT NULL,
                domain VARCHAR(255) NULL,
                career_url TEXT NULL,
                source_url TEXT NULL,
                source_type VARCHAR(50) NOT NULL DEFAULT 'SearXNG',
                country VARCHAR(100) NULL,
                state VARCHAR(100) NULL,
                city VARCHAR(150) NULL,
                latitude DECIMAL(10,7) NULL,
                longitude DECIMAL(10,7) NULL,
                distance_miles DECIMAL(8,2) NULL,
                work_arrangement VARCHAR(20) NULL,
                listing_skills TEXT NULL,
                listing_details TEXT NULL,
                usa_credibility INT NULL,
                date_found TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                last_checked TIMESTAMP NULL DEFAULT NULL,
                result_updated_at TIMESTAMP NULL DEFAULT NULL,
                is_kept TINYINT(1) NOT NULL DEFAULT 0,
                job_open_status VARCHAR(20) NOT NULL DEFAULT 'Open',
                application_status VARCHAR(30) NOT NULL DEFAULT 'None',
                notes TEXT NULL,
                is_rejected TINYINT(1) NOT NULL DEFAULT 0,
                rejection_reason VARCHAR(40) NULL,
                rejected_at TIMESTAMP NULL DEFAULT NULL,
                pre_reject_kept TINYINT(1) NULL,
                pre_reject_status VARCHAR(30) NULL
            )
        """)
        connection.commit()
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None and connection.is_connected():
            connection.close()


def ensure_keep_column():
    if "keep" in _schema_ready:
        return
    connection = None
    cursor = None

    try:
        connection = db.connect()
        cursor = connection.cursor()
        cursor.execute("""
            ALTER TABLE companies
            ADD COLUMN IF NOT EXISTS is_kept TINYINT(1) NOT NULL DEFAULT 0
        """)
        connection.commit()
        _schema_ready.add("keep")
    except Error as error:
        print()
        print("Could not ensure the keep column exists.")
        print(error)
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None and connection.is_connected():
            connection.close()


def ensure_job_tracking_columns():
    if "tracking" in _schema_ready:
        return
    connection = None
    cursor = None

    try:
        connection = db.connect()
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
        cursor.execute("""
            ALTER TABLE companies
            ADD COLUMN IF NOT EXISTS job_open_status VARCHAR(20) NOT NULL DEFAULT 'Open'
        """)
        cursor.execute("""
            ALTER TABLE companies
            ADD COLUMN IF NOT EXISTS application_status VARCHAR(30) NOT NULL DEFAULT 'None'
        """)
        # Keep unsaved legacy rows from appearing as if the user explicitly saved them.
        cursor.execute("""
            UPDATE companies
            SET application_status = 'None'
            WHERE is_kept = 0 AND is_rejected = 0 AND application_status = 'Saved'
        """)
        cursor.execute("""
            ALTER TABLE companies
            MODIFY COLUMN application_status VARCHAR(30) NOT NULL DEFAULT 'None'
        """)
        cursor.execute("""
            ALTER TABLE companies
            ADD COLUMN IF NOT EXISTS notes TEXT NULL
        """)
        cursor.execute("ALTER TABLE companies ADD COLUMN IF NOT EXISTS listing_skills TEXT NULL")
        cursor.execute("ALTER TABLE companies ADD COLUMN IF NOT EXISTS listing_details TEXT NULL")
        cursor.execute("""
            ALTER TABLE companies
            ADD COLUMN IF NOT EXISTS source_type VARCHAR(50) NOT NULL DEFAULT 'SearXNG'
        """)
        cursor.execute("""
            ALTER TABLE companies
            ADD COLUMN IF NOT EXISTS is_rejected TINYINT(1) NOT NULL DEFAULT 0
        """)
        cursor.execute("ALTER TABLE companies ADD COLUMN IF NOT EXISTS rejection_reason VARCHAR(40) NULL")
        cursor.execute("""
            ALTER TABLE companies
            ADD COLUMN IF NOT EXISTS rejected_at TIMESTAMP NULL DEFAULT NULL
        """)
        # What the listing looked like before rejection, so Restore can put it back.
        cursor.execute("ALTER TABLE companies ADD COLUMN IF NOT EXISTS pre_reject_kept TINYINT(1) NULL")
        # Who rejected it: 'user' (the Reject button) or 'system' (Job Finder's own filters). Older rows are left empty, so they count as the user's.
        cursor.execute("ALTER TABLE companies ADD COLUMN IF NOT EXISTS rejected_by VARCHAR(10) NULL")
        cursor.execute("ALTER TABLE companies ADD COLUMN IF NOT EXISTS pre_reject_status VARCHAR(30) NULL")
        cursor.execute("""ALTER TABLE companies ADD COLUMN IF NOT EXISTS city VARCHAR(150) NULL""")
        cursor.execute("""ALTER TABLE companies ADD COLUMN IF NOT EXISTS latitude DECIMAL(10,7) NULL""")
        cursor.execute("""ALTER TABLE companies ADD COLUMN IF NOT EXISTS longitude DECIMAL(10,7) NULL""")
        cursor.execute("""ALTER TABLE companies ADD COLUMN IF NOT EXISTS distance_miles DECIMAL(8,2) NULL""")
        cursor.execute("""ALTER TABLE companies ADD COLUMN IF NOT EXISTS result_updated_at TIMESTAMP NULL DEFAULT NULL""")
        cursor.execute("""ALTER TABLE companies ADD COLUMN IF NOT EXISTS work_arrangement VARCHAR(20) NULL""")
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS search_history (
                id INT AUTO_INCREMENT PRIMARY KEY,
                job_title TEXT NOT NULL,
                state VARCHAR(100) NOT NULL,
                cities_json TEXT NULL,
                searched_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cursor.execute("""
            ALTER TABLE search_history
            MODIFY COLUMN job_title TEXT NOT NULL
        """)
        cursor.execute("""ALTER TABLE search_history ADD COLUMN IF NOT EXISTS cities_json TEXT NULL""")
        # One row per posting rather than per website, so an employer can have many openings.
        index_status = ensure_unique_source_index(cursor)
        if index_status:
            print(f"Database index update: {index_status}")
        connection.commit()
        _schema_ready.add("tracking")
    except Error as error:
        print()
        print("Could not ensure job tracking fields exist.")
        print(error)
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None and connection.is_connected():
            connection.close()


def tidy_expired_closed_jobs():
    """At startup: delete jobs closed for more than a week, keeping saved ones (see job_retention.py)."""
    connection = None
    try:
        connection = db.connect()
        deleted = tidy_closed_jobs(connection)
        if deleted:
            print(f"Deleted {deleted} job(s) closed for more than {CLOSED_KEEP_DAYS} days (saved jobs are kept).")
    except Error as error:
        print()
        print("Could not tidy closed jobs.")
        print(error)
    finally:
        if connection is not None and connection.is_connected():
            connection.close()


WORK_DETAIL_COLUMNS = (("street", 200), ("city", 100), ("state", 50), ("zip", 20), ("phone", 40), ("website", 255),
                       ("supervisor_name", 150), ("supervisor_title", 150), ("supervisor_email", 255))


# Education: the degree is one of these (the drop-down's choices), and each field's maximum length.
EDUCATION_DEGREES = ["High School Diploma", "GED", "Certificate", "Associate's Degree", "Bachelor's Degree", "Master's Degree",
                     "Doctorate", "Professional Degree", "Some College (No Degree)", "Other"]
EDUCATION_FIELDS = (("school", 200), ("degree", 60), ("major", 150), ("minor", 150), ("start_date", 20), ("end_date", 20), ("gpa", 10))


def ensure_profile_tables():
    if "profile" in _schema_ready:
        return
    connection = None
    cursor = None

    try:
        connection = db.connect()
        cursor = connection.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS user_profile (
                id TINYINT PRIMARY KEY,
                first_name VARCHAR(100) NOT NULL DEFAULT '',
                last_name VARCHAR(100) NOT NULL DEFAULT '',
                state VARCHAR(100) NOT NULL DEFAULT '',
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS user_profile_job_titles (
                id INT AUTO_INCREMENT PRIMARY KEY,
                profile_id TINYINT NOT NULL,
                job_title VARCHAR(255) NOT NULL,
                UNIQUE KEY unique_profile_job_title (profile_id, job_title)
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS user_profile_cities (
                id INT AUTO_INCREMENT PRIMARY KEY,
                profile_id TINYINT NOT NULL,
                city VARCHAR(150) NOT NULL,
                radius_miles INT NOT NULL DEFAULT 50,
                UNIQUE KEY unique_profile_city (profile_id, city)
            )
        """)
        for column, definition in (
            ("home_location", "VARCHAR(150) NOT NULL DEFAULT ''"),
            ("home_zip", "VARCHAR(10) NOT NULL DEFAULT ''"),
            ("primary_job_title", "VARCHAR(255) NOT NULL DEFAULT ''"),
            ("avatar_data", "MEDIUMTEXT NULL"),
            ("work_preferences", "TEXT NULL"),
        ):
            cursor.execute(f"ALTER TABLE user_profile ADD COLUMN IF NOT EXISTS {column} {definition}")
        cursor.execute("""CREATE TABLE IF NOT EXISTS user_profile_skills (
            id INT AUTO_INCREMENT PRIMARY KEY, profile_id TINYINT NOT NULL,
            skill VARCHAR(80) NOT NULL, UNIQUE KEY unique_profile_skill (profile_id, skill))""")
        cursor.execute("""CREATE TABLE IF NOT EXISTS user_profile_work_history (
            id INT AUTO_INCREMENT PRIMARY KEY, profile_id TINYINT NOT NULL,
            company VARCHAR(150) NOT NULL DEFAULT '', role VARCHAR(150) NOT NULL DEFAULT '',
            dates VARCHAR(100) NOT NULL DEFAULT '', description TEXT NULL)""")
        cursor.execute("""CREATE TABLE IF NOT EXISTS user_profile_education (
            id INT AUTO_INCREMENT PRIMARY KEY, profile_id TINYINT NOT NULL,
            school VARCHAR(200) NOT NULL DEFAULT '', degree VARCHAR(60) NOT NULL DEFAULT '',
            major VARCHAR(150) NOT NULL DEFAULT '', minor VARCHAR(150) NOT NULL DEFAULT '',
            start_date VARCHAR(20) NOT NULL DEFAULT '', end_date VARCHAR(20) NOT NULL DEFAULT '',
            gpa VARCHAR(10) NOT NULL DEFAULT '')""")
        # Optional details some applications ask for: where the job was, its phone and website, and the supervisor.
        for column, size in WORK_DETAIL_COLUMNS:
            cursor.execute(f"ALTER TABLE user_profile_work_history ADD COLUMN IF NOT EXISTS {column} VARCHAR({size}) NOT NULL DEFAULT ''")
        cursor.execute("""
            INSERT IGNORE INTO user_profile (id, first_name, last_name, state)
            VALUES (1, '', '', '')
        """)
        connection.commit()
        _schema_ready.add("profile")
    except Error as error:
        print()
        print("Could not ensure profile tables exist.")
        print(error)
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None and connection.is_connected():
            connection.close()
