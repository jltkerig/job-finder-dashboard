"""The Recent Searches list: recording a search, and showing the main title with the others small."""

import json

from mysql.connector import Error

from jobfinder import db
from jobfinder.profiles.onet_data import proper_title
from jobfinder.web import schema


def record_search_history(job_title, state, cities=None):
    job_title = ", ".join(proper_title(part) for part in str(job_title or "").split(",") if part.strip())
    schema.ensure_job_tracking_columns()
    connection = None
    cursor = None
    try:
        connection = db.connect()
        cursor = connection.cursor()
        cities_text = json.dumps(cities or [])
        # The same search again (same titles, state and cities) just moves the earlier entry back to the top, so it also
        # stays the "latest search" that job_finder.py reads. Nothing is added.
        cursor.execute(
            "SELECT id FROM search_history WHERE LOWER(TRIM(job_title)) = %s AND state = %s AND COALESCE(cities_json, '[]') = %s "
            "ORDER BY searched_at DESC LIMIT 1",
            (str(job_title).strip().lower(), state, cities_text),
        )
        earlier = cursor.fetchone()
        if earlier:
            cursor.execute("UPDATE search_history SET searched_at = CURRENT_TIMESTAMP WHERE id = %s", (earlier[0],))
        else:
            cursor.execute(
                "INSERT INTO search_history (job_title, state, cities_json) VALUES (%s, %s, %s)",
                (job_title, state, cities_text),
            )
        connection.commit()
    except Error as error:
        print("Could not record search history.")
        print(error)
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None and connection.is_connected():
            connection.close()


def split_search_titles(job_title):
    """(main title, other titles) from a search's stored comma-separated title list."""
    parts = [part.strip() for part in str(job_title or "").split(",") if part.strip()]
    return (parts[0] if parts else ""), parts[1:]


def collapse_search_history(rows, limit=10):
    """Newest first, one entry for each distinct search (same titles, state and cities), main/other titles split."""
    seen, result = set(), []
    for row in rows:
        key = (str(row.get("job_title") or "").strip().lower(), str(row.get("state") or "").strip().lower(),
               str(row.get("cities_json") or "[]").strip())
        if key in seen:
            continue
        seen.add(key)
        row["main_title"], row["other_titles"] = split_search_titles(row.get("job_title"))
        result.append(row)
        if len(result) >= limit:
            break
    return result


def get_search_history(limit=10):
    schema.ensure_job_tracking_columns()
    connection = None
    cursor = None
    try:
        connection = db.connect()
        cursor = connection.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT job_title, state, cities_json, searched_at
            FROM search_history
            ORDER BY searched_at DESC
            LIMIT %s
            """,
            (max(limit * 10, 100),),
        )
        return collapse_search_history(cursor.fetchall(), limit)
    except Error as error:
        print("Could not read search history.")
        print(error)
        return []
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None and connection.is_connected():
            connection.close()
