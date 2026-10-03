"""Your saved profile (name, titles, places, skills, work history) and the suggestions made from it."""

import json
import re

from mysql.connector import Error

from jobfinder import db
from jobfinder.profiles.onet_data import occupation_skill_suggestions, proper_title, related_title_suggestions
from jobfinder.profiles.profile_tools import SKILL_ALIASES, normalize_skills, refresh_listing_skills, skill_demand, related_skills
from jobfinder.web.schema import ensure_profile_tables

RELATED_JOB_TITLES = {
    "web designer": ["UI Designer", "UX/UI Designer", "Digital Designer", "Website Designer", "Visual Designer", "WordPress Designer"],
    "front end developer": ["Frontend Developer", "Web Developer", "UI Developer", "Junior Web Developer", "WordPress Developer", "Web Content Developer"],
    "frontend developer": ["Front End Developer", "Web Developer", "UI Developer", "Junior Web Developer", "WordPress Developer", "Web Content Developer"],
    "web developer": ["Front End Developer", "Frontend Developer", "Junior Web Developer", "WordPress Developer", "UI Developer", "Web Application Developer"],
    "wordpress developer": ["Web Developer", "Front End Developer", "WordPress Designer", "Website Developer", "PHP Developer", "Web Content Developer"],
    "ui designer": ["Web Designer", "UX/UI Designer", "Visual Designer", "Product Designer", "Digital Designer", "Interaction Designer"],
    "ux designer": ["UX/UI Designer", "UI Designer", "Product Designer", "Interaction Designer", "Web Designer", "Experience Designer"],
    "ux/ui designer": ["UI Designer", "UX Designer", "Product Designer", "Web Designer", "Interaction Designer", "Digital Designer"],
    "graphic designer": ["Digital Designer", "Visual Designer", "Web Designer", "Production Designer", "Marketing Designer", "Brand Designer"],
    "website content coordinator": ["Web Content Coordinator", "Web Content Specialist", "Content Coordinator", "CMS Specialist", "Digital Content Specialist", "Website Coordinator"],
    "web content coordinator": ["Website Content Coordinator", "Web Content Specialist", "Content Coordinator", "CMS Specialist", "Digital Content Specialist", "Website Coordinator"],
    "content coordinator": ["Web Content Coordinator", "Website Content Coordinator", "Content Specialist", "Digital Content Specialist", "CMS Specialist", "Marketing Coordinator"],
}


def related_job_title_suggestions(raw_titles):
    selected = [part.strip() for part in (raw_titles or "").split(",") if part.strip()]
    selected_lower = {title.lower() for title in selected}
    suggestions = []

    for title in selected:
        key = re.sub(r"\s+", " ", title.lower()).strip()
        candidates = RELATED_JOB_TITLES.get(key, []) + related_title_suggestions(title)

        if not candidates:
            if "designer" in key:
                candidates = ["Web Designer", "UI Designer", "UX/UI Designer", "Digital Designer", "Visual Designer"]
            elif "developer" in key:
                candidates = ["Web Developer", "Front End Developer", "Frontend Developer", "UI Developer", "WordPress Developer"]
            elif "content" in key:
                candidates = ["Web Content Specialist", "Content Coordinator", "Digital Content Specialist", "CMS Specialist", "Website Coordinator"]

        for candidate in candidates:
            candidate_key = candidate.lower()
            if candidate_key not in selected_lower and candidate_key not in {item.lower() for item in suggestions}:
                suggestions.append(proper_title(candidate))

    return suggestions[:8]


def profile_skill_suggestions(profile):
    selected = profile.get("primary_job_title") or next(iter(profile.get("job_titles") or []), "")
    # Use occupation examples to rank skills our listing parser can also recognize.
    aliases = {alias.casefold(): name for name, variants in SKILL_ALIASES.items()
               for alias in [name, *variants]}
    ranked = []
    for example in occupation_skill_suggestions(selected, limit=150):
        lower = example.casefold()
        canonical = aliases.get(lower)
        if canonical is None:
            canonical = next((name for alias, name in aliases.items()
                              if len(alias) >= 3 and re.search(r"(?<!\w)" + re.escape(alias) + r"(?!\w)", lower)), None)
        if canonical and canonical not in ranked:
            ranked.append(canonical)
    saved = {skill.casefold() for skill in profile.get("skills", [])}
    return [skill for skill in dict.fromkeys(ranked + list(SKILL_ALIASES))
            if skill.casefold() not in saved]


def refresh_job_fit():
    """Re-read stored listings for skills the current skills list recognizes. Returns how many listings changed."""
    connection = None
    try:
        connection = db.connect()
        return refresh_listing_skills(connection)
    except Error as error:
        print(f"Could not refresh Job Fit: {error}")
        return 0
    finally:
        if connection is not None and connection.is_connected():
            connection.close()


def listing_skill_demand(saved_skills, limit=12):
    """The skills most often named by the jobs Job Finder has found (not rejected) that your profile doesn't list."""
    connection = None
    cursor = None
    try:
        connection = db.connect()
        cursor = connection.cursor()
        cursor.execute("SELECT listing_skills FROM companies WHERE is_rejected = 0 AND listing_skills IS NOT NULL")
        return skill_demand([row[0] for row in cursor.fetchall()], saved_skills, limit)
    except Error as error:
        print(f"Could not read the skills asked for in listings: {error}")
        return []
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None and connection.is_connected():
            connection.close()


def get_user_profile():
    ensure_profile_tables()
    connection = None
    cursor = None

    profile = {
        "first_name": "",
        "last_name": "",
        "state": "",
        "home_location": "", "home_zip": "", "primary_job_title": "", "avatar_data": "", "skills": [], "work_history": [], "work_preferences": [],
        "job_titles": [],
        "cities": [],
    }

    try:
        connection = db.connect()
        cursor = connection.cursor(dictionary=True)
        cursor.execute("""
            SELECT first_name, last_name, state, home_location, home_zip, primary_job_title, avatar_data, work_preferences
            FROM user_profile
            WHERE id = 1
        """)
        row = cursor.fetchone()
        if row:
            profile.update(row)
            try:
                profile["work_preferences"] = json.loads(row.get("work_preferences") or "[]")
            except ValueError:
                profile["work_preferences"] = []

        cursor.execute("""
            SELECT job_title
            FROM user_profile_job_titles
            WHERE profile_id = 1
            ORDER BY job_title
        """)
        profile["job_titles"] = [row["job_title"] for row in cursor.fetchall()]
        cursor.execute("""
            SELECT city, radius_miles
            FROM user_profile_cities
            WHERE profile_id = 1
            ORDER BY city
        """)
        profile["cities"] = cursor.fetchall()
        cursor.execute("SELECT skill FROM user_profile_skills WHERE profile_id = 1 ORDER BY skill")
        profile["skills"] = [row["skill"] for row in cursor.fetchall()]
        cursor.execute("SELECT company, role, dates, description FROM user_profile_work_history WHERE profile_id = 1 ORDER BY id")
        profile["work_history"] = cursor.fetchall()
        # Titles are always shown properly capitalized; searching ignores case, so this never changes what is found.
        profile["primary_job_title"] = proper_title(profile.get("primary_job_title") or "")
        profile["job_titles"] = list(dict.fromkeys(proper_title(title) for title in profile["job_titles"]))
        return profile
    except Error as error:
        print()
        print("Could not read user profile from the database.")
        print(error)
        return profile
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None and connection.is_connected():
            connection.close()


def save_user_profile(first_name, last_name, state, job_titles, cities=None, *, home_location=None, home_zip=None,
                      primary_job_title=None, skills=None, work_history=None, avatar_data=None, work_preferences=None):
    ensure_profile_tables()
    connection = None
    cursor = None

    try:
        connection = db.connect()
        cursor = connection.cursor()
        cursor.execute("""
            UPDATE user_profile
            SET first_name = %s, last_name = %s, state = %s
            WHERE id = 1
        """, (first_name, last_name, state))
        if home_location is not None:
            cursor.execute("UPDATE user_profile SET home_location = %s WHERE id = 1", (home_location[:150],))
        if home_zip is not None:
            cursor.execute("UPDATE user_profile SET home_zip = %s WHERE id = 1", (home_zip,))
        if primary_job_title is not None:
            cursor.execute("UPDATE user_profile SET primary_job_title = %s WHERE id = 1", (primary_job_title[:255],))
        if avatar_data is not None:
            cursor.execute("UPDATE user_profile SET avatar_data = %s WHERE id = 1", (avatar_data,))
        if work_preferences is not None:
            cursor.execute("UPDATE user_profile SET work_preferences = %s WHERE id = 1", (json.dumps(work_preferences),))
        cursor.execute("DELETE FROM user_profile_job_titles WHERE profile_id = 1")

        for job_title in job_titles:
            cursor.execute("""
                INSERT INTO user_profile_job_titles (profile_id, job_title)
                VALUES (1, %s)
            """, (job_title,))

        cursor.execute("DELETE FROM user_profile_cities WHERE profile_id = 1")
        allowed_radii = {5, 10, 15, 20, 30, 50}
        for item in (cities or []):
            city = str(item.get("city", "")).strip()[:150]
            try:
                radius = int(item.get("radius", item.get("radius_miles", 50)))
            except (TypeError, ValueError):
                radius = 50
            if city and radius in allowed_radii:
                cursor.execute("""
                    INSERT IGNORE INTO user_profile_cities (profile_id, city, radius_miles)
                    VALUES (1, %s, %s)
                    """, (city, radius))

        if skills is not None:
            cursor.execute("DELETE FROM user_profile_skills WHERE profile_id = 1")
            for skill in normalize_skills(skills):
                cursor.execute("INSERT INTO user_profile_skills (profile_id, skill) VALUES (1, %s)", (skill,))
        if work_history is not None:
            cursor.execute("DELETE FROM user_profile_work_history WHERE profile_id = 1")
            for item in work_history[:50]:
                if not isinstance(item, dict):
                    continue
                company = str(item.get("company", "")).strip()[:150]
                role = str(item.get("role", "")).strip()[:150]
                dates = str(item.get("dates", "")).strip()[:100]
                description = str(item.get("description", "")).strip()[:3000]
                if company or role:
                    cursor.execute("""INSERT INTO user_profile_work_history
                        (profile_id, company, role, dates, description) VALUES (1, %s, %s, %s, %s)""",
                        (company, role, dates, description))

        connection.commit()
        return True
    except Error as error:
        print()
        print("Could not save user profile.")
        print(error)
        return False
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None and connection.is_connected():
            connection.close()


def related_skills_for(term, limit=8):
    """Skills that go with `term`, from the curated list and from the skills named together in the jobs found."""
    lists = []
    try:
        with db.cursor() as cursor:
            cursor.execute("SELECT listing_skills FROM companies WHERE is_rejected = 0 AND listing_skills IS NOT NULL "
                           "AND listing_skills LIKE %s LIMIT 800", (f"%{str(term)[:60]}%",))
            lists = [row[0] for row in cursor.fetchall()]
    except Error as error:
        print(f"Could not read related skills from the listings: {error}")
    return related_skills(term, lists, limit)
