"""Deleting jobs that have been closed for a while, so the database doesn't keep growing.

closed_at records when a row was first seen as Closed. Rather than touching every place that sets
job_open_status, sync_closed_dates() stamps it after each update, import and dashboard start.
Saved jobs (kept, or marked Saved / Applied / Talking With Recruiter / Interview) are never deleted.
"""

CLOSED_KEEP_DAYS = 7  # a closed job is also hidden from the results at once; saved ones are never deleted
UNSAVED_KEEP_DAYS = 14  # an unsaved result is kept this long after it was first found, then deleted
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


def delete_old_unsaved(cursor, days=UNSAVED_KEEP_DAYS):
    """Delete results first found more than `days` days ago that you never saved, applied to or queued.

    date_found is when this computer first saved the job (it is not changed when the job is seen again), so this
    counts how long it has been here, not how old the posting is. Rejected jobs stay: they are how a rejected job
    is recognised when it turns up again. Returns how many were deleted.
    """
    placeholders = ", ".join(["%s"] * len(SAVED_STATUSES))
    cursor.execute(
        f"""
        DELETE FROM companies
        WHERE is_rejected = 0
          AND is_kept = 0
          AND COALESCE(application_status, 'None') NOT IN ({placeholders})
          AND COALESCE(listing_details, '') NOT LIKE '%%"apply_queued": true%%'
          AND date_found < CURRENT_TIMESTAMP - INTERVAL %s DAY
        """,
        (*SAVED_STATUSES, int(days)),
    )
    return cursor.rowcount


def tidy_old_jobs(connection, days=UNSAVED_KEEP_DAYS):
    """Delete unsaved results that have been here more than `days` days. Returns how many were deleted."""
    cursor = connection.cursor()
    try:
        deleted = delete_old_unsaved(cursor, days)
        connection.commit()
        return deleted
    finally:
        cursor.close()


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
