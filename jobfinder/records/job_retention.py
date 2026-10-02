"""Deleting jobs that have been closed for a while, so the database doesn't keep growing.

closed_at records when a row was first seen as Closed. Rather than touching every place that sets
job_open_status, sync_closed_dates() stamps it after each update, import and dashboard start.
Saved jobs (kept, or marked Saved / Applied / Talking With Recruiter / Interview) are never deleted.
"""

CLOSED_KEEP_DAYS = 7  # a closed job is also hidden from the results at once; saved ones are never deleted
SAVED_STATUSES = ("Saved", "Applied", "Talking With Recruiter", "Interview")


def ensure_closed_at_column(cursor):
    cursor.execute("ALTER TABLE companies ADD COLUMN IF NOT EXISTS closed_at TIMESTAMP NULL DEFAULT NULL")


def sync_closed_dates(cursor):
    """Stamp rows that just became Closed and clear the stamp on rows that reopened."""
    cursor.execute("UPDATE companies SET closed_at = CURRENT_TIMESTAMP WHERE job_open_status = 'Closed' AND closed_at IS NULL")
    cursor.execute("UPDATE companies SET closed_at = NULL WHERE job_open_status <> 'Closed' AND closed_at IS NOT NULL")


def delete_expired_closed(cursor, days=CLOSED_KEEP_DAYS):
    """Delete unsaved rows closed for more than `days` days. Returns how many were deleted."""
    placeholders = ", ".join(["%s"] * len(SAVED_STATUSES))
    cursor.execute(
        f"""
        DELETE FROM companies
        WHERE job_open_status = 'Closed'
          AND closed_at IS NOT NULL
          AND closed_at < CURRENT_TIMESTAMP - INTERVAL %s DAY
          AND is_kept = 0
          AND application_status NOT IN ({placeholders})
        """,
        (int(days), *SAVED_STATUSES),
    )
    return cursor.rowcount


def tidy_closed_jobs(connection, days=CLOSED_KEEP_DAYS):
    """Stamp close dates and delete expired closed jobs in one step. Returns how many were deleted."""
    cursor = connection.cursor()
    try:
        ensure_closed_at_column(cursor)
        sync_closed_dates(cursor)
        deleted = delete_expired_closed(cursor, days)
        connection.commit()
        return deleted
    finally:
        cursor.close()
