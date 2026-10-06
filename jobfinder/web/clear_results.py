"""Clear Results on the Search page: delete the listings you haven't acted on, so the next search starts fresh.
Saved, queued and applied jobs stay, and rejected ones stay rejected so the next search doesn't bring them back."""

from flask import jsonify
from mysql.connector import Error

from jobfinder import db
from jobfinder.web import search_control
from jobfinder.web.core import api_error, app

CLEARABLE = ("is_kept = 0 AND is_rejected = 0 AND COALESCE(application_status, 'None') IN ('None', '') "
             "AND COALESCE(listing_details, '') NOT LIKE '%\"apply_queued\": true%'")


@app.route("/clear-results", methods=["POST"])
def clear_results():
    process = search_control.scraper_process
    if process is not None and process.poll() is None:
        return api_error("E3250", "A search is running. Clear the results after it finishes.", 409)
    connection = cursor = None
    try:
        connection = db.connect()
        cursor = connection.cursor()
        cursor.execute(f"DELETE FROM companies WHERE {CLEARABLE}")
        connection.commit()
        return jsonify({"status": "cleared", "count": cursor.rowcount})
    except Error as error:
        return api_error("E3250", f"Could not clear the results: {error}", 500)
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()
