"""Database index maintenance shared by the dashboard and job_finder."""


def ensure_unique_source_index(cursor):
    """One row per posting, not per website: an employer can have many openings.

    Drops the old unique key on domain and makes the posting URL the unique key.
    No rows are changed. If two rows already share a URL, a plain index is added
    instead so nothing is lost. Returns a short status string for logging.
    """
    cursor.execute("SHOW INDEX FROM companies")
    names = {row[2] for row in cursor.fetchall()}
    status = []
    if "domain_unique" in names:
        cursor.execute("ALTER TABLE companies DROP INDEX domain_unique")
        names.discard("domain_unique")
        status.append("dropped domain_unique")
    if "domain_lookup" not in names:
        cursor.execute("ALTER TABLE companies ADD INDEX domain_lookup (domain)")
        status.append("added domain_lookup")
    if "source_url_unique" in names:
        return ", ".join(status)
    cursor.execute(
        "SELECT COUNT(*) FROM (SELECT 1 FROM companies WHERE source_url IS NOT NULL "
        "GROUP BY LEFT(source_url, 500) HAVING COUNT(*) > 1) duplicated"
    )
    if cursor.fetchone()[0] == 0:
        cursor.execute("ALTER TABLE companies ADD UNIQUE KEY source_url_unique (source_url(500))")
        status.append("added source_url_unique")
    elif "source_url_lookup" not in names:
        cursor.execute("ALTER TABLE companies ADD INDEX source_url_lookup (source_url(500))")
        status.append("duplicate source URLs found; added source_url_lookup instead")
    return ", ".join(status)
