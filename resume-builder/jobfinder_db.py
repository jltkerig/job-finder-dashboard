"""Access to the Job Finder database.

Everything is read-only except save_profile(), which writes the same profile fields Job
Finder's own Dashboard form does, the same way, so both apps always show one profile.
"""
import json
import sys
from contextlib import contextmanager

import mysql.connector

import config


class JobFinderUnavailable(RuntimeError):
    pass


@contextmanager
def _cursor(commit=False):
    try:
        connection = mysql.connector.connect(
            host=config.DB_HOST, port=config.DB_PORT, user=config.DB_USER,
            password=config.DB_PASSWORD, database=config.DB_NAME, connection_timeout=5,
        )
    except mysql.connector.Error as error:
        raise JobFinderUnavailable(
            "Cannot reach the Job Finder database. Start Job Finder (or MySQL in XAMPP) and try again."
        ) from error
    cursor = connection.cursor(dictionary=True)
    try:
        yield cursor
        if commit:
            connection.commit()
    finally:
        cursor.close()
        connection.close()


# Optional details for each job (the same columns Job Finder's schema adds), with their maximum lengths.
WORK_DETAIL_SIZES = {"street": 200, "city": 100, "state": 50, "zip": 20, "phone": 40, "website": 255,
                     "supervisor_name": 150, "supervisor_title": 150, "supervisor_email": 255,
                     "supervisor_phone": 40}
WORK_DETAILS = tuple(WORK_DETAIL_SIZES)
# Education, as Job Finder stores it (its schema.EDUCATION_DEGREES / EDUCATION_FIELDS).
EDUCATION_DEGREES = ["High School Diploma", "GED", "Certificate", "Associate's Degree", "Bachelor's Degree", "Master's Degree",
                     "Doctorate", "Professional Degree", "Some College (No Degree)", "Other"]
EDUCATION_SIZES = {"school": 200, "degree": 60, "major": 150, "minor": 150, "start_date": 20, "end_date": 20, "gpa": 10}


def _table_exists(cursor, name):
    cursor.execute("SHOW TABLES LIKE %s", (name,))
    return cursor.fetchone() is not None


def get_profile():
    profile = {"first_name": "", "last_name": "", "home_location": "", "state": "", "linkedin_url": "", "portfolio_url": "", "home_zip": "",
               "primary_job_title": "", "job_titles": [], "skills": [], "work_history": [], "education": []}
    with _cursor() as cursor:
        if not _table_exists(cursor, "user_profile"):
            return profile
        cursor.execute("SELECT * FROM user_profile WHERE id = 1")
        row = cursor.fetchone() or {}
        for key in ("first_name", "last_name", "home_location", "state", "primary_job_title", "linkedin_url", "portfolio_url", "home_zip"):
            profile[key] = (row.get(key) or "").strip()
        if _table_exists(cursor, "user_profile_job_titles"):
            cursor.execute("SELECT job_title FROM user_profile_job_titles WHERE profile_id = 1 ORDER BY id")
            profile["job_titles"] = [r["job_title"] for r in cursor.fetchall()]
        if _table_exists(cursor, "user_profile_skills"):
            cursor.execute("SELECT skill FROM user_profile_skills WHERE profile_id = 1 ORDER BY skill")
            profile["skills"] = [r["skill"] for r in cursor.fetchall()]
        if _table_exists(cursor, "user_profile_work_history"):
            cursor.execute("SELECT * FROM user_profile_work_history WHERE profile_id = 1 ORDER BY id")
            keys = ("company", "role", "dates", "description") + WORK_DETAILS
            profile["work_history"] = [{k: (r.get(k) or "") for k in keys} for r in cursor.fetchall()]
        if _table_exists(cursor, "user_profile_education"):
            cursor.execute("SELECT * FROM user_profile_education WHERE profile_id = 1 ORDER BY id")
            profile["education"] = [{k: (r.get(k) or "") for k in EDUCATION_SIZES} for r in cursor.fetchall()]
    return profile


def clean_contact(contact):
    """{"home_zip", "linkedin_url", "portfolio_url"} as Job Finder stores them: a five-digit ZIP or nothing, and web
    addresses with https:// added when left off (anything else is dropped)."""
    import re
    zip_code = re.sub(r"\D", "", str(contact.get("home_zip", "") or ""))[:5]
    cleaned = {"home_zip": zip_code if len(zip_code) == 5 else ""}
    for key in ("linkedin_url", "portfolio_url"):
        link = str(contact.get(key, "") or "").strip()[:255]
        if link and not re.match(r"^https?://", link, re.I):
            link = "https://" + link
        cleaned[key] = link if re.fullmatch(r"https?://[^\s<>\"']+\.[^\s<>\"']+", link, re.I) else ""
    return cleaned


def clean_education(entries):
    """Schools without a name are dropped; a degree that isn't one of the choices becomes "Other"."""
    cleaned = []
    for item in (entries or [])[:20]:
        row = {k: str(item.get(k, "") or "").strip()[:size] for k, size in EDUCATION_SIZES.items()}
        if row["degree"] and row["degree"] not in EDUCATION_DEGREES:
            row["degree"] = "Other"
        if row["school"]:
            cleaned.append(row)
    return cleaned


def _normalize_skills(skills):
    """Job Finder's own skill tidying ("html" -> "HTML", no duplicates), so both apps agree."""
    if str(config.JOB_FINDER_DIR) not in sys.path:
        sys.path.append(str(config.JOB_FINDER_DIR))
    try:
        from jobfinder.profiles.profile_tools import normalize_skills
    except ImportError:  # Job Finder moved: fall back to a plain de-duplicate
        seen, result = set(), []
        for skill in (str(s).strip()[:80] for s in skills):
            if skill and skill.casefold() not in seen:
                seen.add(skill.casefold())
                result.append(skill)
        return result[:100]
    return normalize_skills(skills)


def _titles(primary, others):
    """Primary first, then the others without repeats, as Job Finder's /save-profile does."""
    titles = []
    for title in [primary, *others]:
        title = (title or "").strip()[:255]
        if title and title.casefold() not in {t.casefold() for t in titles}:
            titles.append(title)
    return titles


def save_profile(first_name, last_name, home_location, primary_job_title, other_titles, skills, work_history, education=None,
                 contact=None):
    """Write the profile fields Resume Builder shows. Cities, photo and work preferences are left alone.
    State is the part of the home city after the last comma, exactly as Job Finder's form sets it."""
    first_name, last_name = first_name.strip()[:100], last_name.strip()[:100]
    home_location = home_location.strip()[:150]
    state = home_location.split(",")[-1].strip()[:100] if "," in home_location else ""
    primary = (primary_job_title or "").strip()[:255]
    history = []
    sizes = {"company": 150, "role": 150, "dates": 100, "description": 3000, **WORK_DETAIL_SIZES}
    for item in work_history[:50]:
        row = {k: str(item.get(k, "") or "").strip()[:size] for k, size in sizes.items()}
        if row["company"] or row["role"]:
            history.append(row)
    with _cursor(commit=True) as cursor:
        if not _table_exists(cursor, "user_profile"):
            raise JobFinderUnavailable("Job Finder's profile isn't set up yet. Open Job Finder's Dashboard once, then try again.")
        cursor.execute("INSERT IGNORE INTO user_profile (id, first_name, last_name, state) VALUES (1, '', '', '')")
        cursor.execute("UPDATE user_profile SET first_name = %s, last_name = %s, home_location = %s, state = %s, "
                       "primary_job_title = %s WHERE id = 1", (first_name, last_name, home_location, state, primary))
        if contact:  # home ZIP and links, written the way Job Finder's Dashboard writes them
            cursor.execute("SHOW COLUMNS FROM user_profile")
            present = {r["Field"] for r in cursor.fetchall()}
            for column, value in clean_contact(contact).items():
                if column in present:
                    cursor.execute(f"UPDATE user_profile SET {column} = %s WHERE id = 1", (value,))
        cursor.execute("DELETE FROM user_profile_job_titles WHERE profile_id = 1")
        for title in _titles(primary, other_titles):
            cursor.execute("INSERT INTO user_profile_job_titles (profile_id, job_title) VALUES (1, %s)", (title,))
        cursor.execute("DELETE FROM user_profile_skills WHERE profile_id = 1")
        for skill in _normalize_skills(skills):
            cursor.execute("INSERT INTO user_profile_skills (profile_id, skill) VALUES (1, %s)", (skill,))
        # The optional details are saved only where Job Finder has added their columns (it does on start).
        cursor.execute("SHOW COLUMNS FROM user_profile_work_history")
        present = {r["Field"] for r in cursor.fetchall()}
        columns = ["company", "role", "dates", "description"] + [c for c in WORK_DETAILS if c in present]
        cursor.execute("DELETE FROM user_profile_work_history WHERE profile_id = 1")
        for row in history:
            cursor.execute(f"INSERT INTO user_profile_work_history (profile_id, {', '.join(columns)}) "
                           f"VALUES (1{', %s' * len(columns)})", [row[c] for c in columns])
        # Education is only replaced when the form that edits it was saved (None leaves it as it is).
        if education is not None and _table_exists(cursor, "user_profile_education"):
            cursor.execute("DELETE FROM user_profile_education WHERE profile_id = 1")
            for row in clean_education(education):
                cursor.execute(f"INSERT INTO user_profile_education (profile_id, {', '.join(EDUCATION_SIZES)}) "
                               f"VALUES (1{', %s' * len(EDUCATION_SIZES)})", [row[k] for k in EDUCATION_SIZES])


def _listing(details):
    """listing_details is JSON from the web scraper, or plain text from older captures."""
    if not details:
        return {}
    try:
        data = json.loads(details)
    except (TypeError, ValueError):
        return {"description": details}
    return data if isinstance(data, dict) else {"description": str(data)}


def _skills(raw):
    try:
        value = json.loads(raw or "[]")
    except ValueError:
        return []
    return [str(s) for s in value] if isinstance(value, list) else []


def _summary(row):
    place = ", ".join(p for p in (row.get("city"), row.get("state")) if p)
    return {
        "job_id": row["id"],
        "job_title": row.get("career_job_title") or "",
        "company": row.get("name") or "",
        "location": place,
        "work_arrangement": row.get("work_arrangement") or "",
        "application_status": row.get("application_status") or "None",
        "saved": bool(row.get("is_kept")),
        "date_found": row["date_found"].strftime("%Y-%m-%d") if row.get("date_found") else "",
    }


def list_jobs(search="", limit=25):
    """Saved jobs first, then the newest. Rejected listings are left out."""
    limit = max(1, min(int(limit or 25), 100))
    sql = ("SELECT id, name, career_job_title, city, state, work_arrangement, application_status, "
           "is_kept, date_found FROM companies WHERE is_rejected = 0")
    args = []
    if search:
        sql += " AND (name LIKE %s OR career_job_title LIKE %s)"
        args += [f"%{search}%", f"%{search}%"]
    sql += " ORDER BY is_kept DESC, date_found DESC LIMIT %s"
    args.append(limit)
    with _cursor() as cursor:
        cursor.execute(sql, args)
        return [_summary(row) for row in cursor.fetchall()]


# Statuses that take a job off Job Finder's Apply queue (its auto_apply.QUEUE_DONE).
QUEUE_DONE = ("Applied", "Talking With Recruiter", "Interview", "Rejected", "Closed")


def list_apply_queue():
    """Jobs on Job Finder's Apply queue, not yet applied to, oldest first."""
    with _cursor() as cursor:
        cursor.execute("SELECT id, name, career_job_title, city, state, work_arrangement, application_status, is_kept, "
                       "date_found, listing_details FROM companies WHERE listing_details LIKE %s "
                       "AND COALESCE(is_rejected, 0) = 0 ORDER BY id", ('%"apply_queued"%',))
        rows = cursor.fetchall()
    return [_summary(row) for row in rows
            if _listing(row.get("listing_details")).get("apply_queued") and row.get("application_status") not in QUEUE_DONE]


def get_job(job_id):
    with _cursor() as cursor:
        cursor.execute("SELECT * FROM companies WHERE id = %s", (int(job_id),))
        row = cursor.fetchone()
    if not row:
        return None
    job = _summary(row)
    listing = _listing(row.get("listing_details"))
    job.update({
        "description": listing.get("description", ""),
        "salary": listing.get("salary", ""),
        "posted": listing.get("posted", ""),
        "listing_location": listing.get("location", ""),
        "listing_skills": _skills(row.get("listing_skills")),
        "url": row.get("source_url") or row.get("career_url") or "",
        "notes": row.get("notes") or "",
    })
    return job

def profile_version(profile):
    """A short fingerprint of the saved profile. A form carries the one it was loaded with; if the profile has changed
    since (for example in the other app), saving that form would overwrite the change, so it is refused instead."""
    import hashlib
    shared = {key: profile.get(key) for key in ("first_name", "last_name", "home_location", "home_zip", "linkedin_url",
                                                 "portfolio_url", "primary_job_title", "job_titles", "skills", "work_history",
                                                 "education")}
    return hashlib.sha1(json.dumps(shared, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:16]
