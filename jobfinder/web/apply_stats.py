"""Jobs applied to, by month, for the Job Apply Stats box on the Dashboard.

A job counts once it is Applied, Talking With Recruiter or Interview (the same jobs as "Applied In Total"). Its month
comes from the "applied_on" date saved with it (applied_dates.py); jobs applied to before dates existed have none and
are counted apart, so the rows always add up to the total.
"""

from datetime import date, datetime

from mysql.connector import Error

from jobfinder import db
from jobfinder.web import applied_dates, schema, webfiles

MONTHS_SHOWN = 6
APPLIED_STATUSES = ("Applied", "Talking With Recruiter", "Interview")


def recent_months(count=MONTHS_SHOWN, today=None):
    """The last `count` months as (YYYY-MM, "Oct 2026"), the current month first."""
    today = today or datetime.now(webfiles.LOCAL_TIMEZONE).date()
    year, month = today.year, today.month
    months = []
    for _ in range(count):
        months.append((f"{year:04d}-{month:02d}", date(year, month, 1).strftime("%b %Y")))
        month -= 1
        if month == 0:
            year, month = year - 1, 12
    return months


def by_month(rows, today=None):
    """rows: applied jobs, each with a listing_details text.

    Returns {"months": [{"key", "label", "count"}, ...] newest first, "earlier": jobs before the oldest month shown,
    "undated": jobs with no applied date}.
    """
    applied_dates.add_to(rows)
    shown = recent_months(today=today)
    oldest = shown[-1][0]
    counts, earlier, undated = {}, 0, 0
    for row in rows:
        month = row["applied_on"][:7]
        if not month:
            undated += 1
        elif month < oldest:
            earlier += 1
        else:
            counts[month] = counts.get(month, 0) + 1
    return {"months": [{"key": key, "label": label, "count": counts.get(key, 0)} for key, label in shown],
            "earlier": earlier, "undated": undated}


def get_applied_by_month():
    schema.ensure_job_tracking_columns()
    empty = by_month([])
    connection = cursor = None
    try:
        connection = db.connect()
        cursor = connection.cursor(dictionary=True)
        marks = ", ".join(["%s"] * len(APPLIED_STATUSES))
        cursor.execute(f"SELECT listing_details FROM companies WHERE is_kept = 1 AND is_rejected = 0 "
                       f"AND application_status IN ({marks})", APPLIED_STATUSES)
        return by_month(cursor.fetchall())
    except Error as error:
        print("Could not read the jobs applied to by month.")
        print(error)
        return empty
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None and connection.is_connected():
            connection.close()
