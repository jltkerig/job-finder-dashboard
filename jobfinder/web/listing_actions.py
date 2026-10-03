"""What the buttons on a listing do: reject, restore, block a domain, keep, update notes and status,
delete.
"""

from flask import jsonify, redirect, request
from mysql.connector import Error

from jobfinder import db
from jobfinder.sources.job_feeds import FEED_NAMES
from jobfinder.web import schema
from jobfinder.web.blocklists import add_domain_to_blocklist
from jobfinder.web.core import api_error, app
from jobfinder.web.schema import ensure_keep_column
from jobfinder.web.webfiles import APPLICATION_STATUSES


@app.route("/reject-listing/<int:company_id>", methods=["POST"])
def reject_listing(company_id):
    schema.ensure_job_tracking_columns()
    wants_json = request.headers.get("X-Requested-With") == "fetch" or request.accept_mimetypes.best == "application/json"
    connection = None
    cursor = None
    domain = None
    reason = (request.get_json(silent=True) or {}).get("reason") if request.is_json else request.form.get("reason")
    if reason not in {"wrong_role", "wrong_location", "not_a_job", "duplicate", "other"}:
        reason = "other"
    try:
        connection = db.connect()
        cursor = connection.cursor(dictionary=True)
        cursor.execute("SELECT domain FROM companies WHERE id = %s", (company_id,))
        row = cursor.fetchone()
        if not row:
            if wants_json:
                return api_error("E3201", "The listing could not be found.", 404)
            return redirect(request.referrer or "/")

        domain = row.get("domain")
        cursor.execute(
            """
            UPDATE companies
            SET pre_reject_kept = CASE WHEN is_rejected = 0 THEN is_kept ELSE pre_reject_kept END,
                pre_reject_status = CASE WHEN is_rejected = 0 THEN application_status ELSE pre_reject_status END,
                is_rejected = 1, is_kept = 0, application_status = 'Rejected',
                rejected_at = CURRENT_TIMESTAMP, rejection_reason = %s, rejected_by = 'user'
            WHERE id = %s
            """,
            (reason, company_id),
        )
        connection.commit()
        if wants_json:
            return jsonify({"status": "rejected", "company_id": company_id, "domain": domain})
    except Error as error:
        print("Could not reject listing.")
        print(error)
        if wants_json:
            return api_error("E3202", "Could not reject the listing.", 500)
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None and connection.is_connected():
            connection.close()

    return redirect(request.referrer or "/")


@app.route("/restore-rejected/<int:company_id>", methods=["POST"])
def restore_rejected(company_id):
    schema.ensure_job_tracking_columns()
    wants_json = request.headers.get("X-Requested-With") == "fetch" or request.accept_mimetypes.best == "application/json"
    connection = None
    cursor = None
    domain = None
    try:
        connection = db.connect()
        cursor = connection.cursor(dictionary=True)
        cursor.execute("SELECT domain, pre_reject_kept FROM companies WHERE id = %s AND is_rejected = 1", (company_id,))
        row = cursor.fetchone()
        if not row:
            if wants_json:
                return api_error("E3203", "The rejected listing could not be found.", 404)
            return redirect("/rejected-listings")

        domain = row.get("domain")
        # Older rejections have no saved state; they return as unsaved search results.
        cursor.execute(
            """
            UPDATE companies
            SET is_rejected = 0, is_kept = COALESCE(pre_reject_kept, 0),
                application_status = COALESCE(pre_reject_status, 'None'),
                rejected_at = NULL, rejection_reason = NULL, rejected_by = NULL,
                pre_reject_kept = NULL, pre_reject_status = NULL
            WHERE id = %s
            """,
            (company_id,),
        )
        connection.commit()
        if wants_json:
            return jsonify({"status": "restored", "company_id": company_id, "domain": domain,
                            "kept": bool(row.get("pre_reject_kept"))})
    except Error as error:
        print("Could not restore rejected listing.")
        print(error)
        if wants_json:
            return api_error("E3204", "Could not restore the listing.", 500)
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None and connection.is_connected():
            connection.close()

    return redirect("/rejected-listings")


@app.route("/block-domain/<int:company_id>", methods=["POST"])
def block_domain(company_id):
    schema.ensure_job_tracking_columns()
    connection = None
    cursor = None
    try:
        connection = db.connect()
        cursor = connection.cursor(dictionary=True)
        cursor.execute("SELECT domain, source_type FROM companies WHERE id = %s", (company_id,))
        row = cursor.fetchone()
        if row and row.get("source_type") in FEED_NAMES:
            return api_error("E3212", "This is the feed domain, not the employer domain. Block the company instead.", 400)
        if not row or not row.get("domain"):
            return api_error("E3210", "No domain was available to block.", 404)
        add_domain_to_blocklist(row["domain"])
        return jsonify({"status": "blocked", "domain": row["domain"]})
    except Error as error:
        return api_error("E3211", "Could not block the domain.", 500)
    finally:
        if cursor is not None: cursor.close()
        if connection is not None and connection.is_connected(): connection.close()


@app.route("/save-kept", methods=["POST"])
def save_kept():
    ensure_keep_column()

    data = request.get_json(silent=True) or {}
    company_ids = data.get("company_ids", [])

    if not isinstance(company_ids, list):
        return jsonify({"status": "error", "message": "Invalid company list."}), 400

    cleaned_ids = []
    for company_id in company_ids:
        try:
            cleaned_ids.append(int(company_id))
        except (TypeError, ValueError):
            continue

    if not cleaned_ids:
        return jsonify({"status": "error", "message": "Select at least one result to keep."}), 400

    connection = None
    cursor = None

    try:
        connection = db.connect()
        cursor = connection.cursor()
        placeholders = ",".join(["%s"] * len(cleaned_ids))
        cursor.execute(
            f"UPDATE companies SET is_kept = 1, application_status = 'Saved' WHERE id IN ({placeholders})",
            cleaned_ids,
        )
        connection.commit()
        return jsonify({"status": "saved", "count": cursor.rowcount})
    except Error as error:
        return jsonify({"status": "error", "message": str(error)}), 500
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None and connection.is_connected():
            connection.close()


@app.route("/unsave-kept/<int:company_id>", methods=["POST"])
def unsave_kept(company_id):
    schema.ensure_job_tracking_columns()
    connection = None
    cursor = None
    try:
        connection = db.connect()
        cursor = connection.cursor()
        cursor.execute(
            "UPDATE companies SET is_kept = 0, application_status = 'None' WHERE id = %s",
            (company_id,),
        )
        connection.commit()
        return jsonify({"status": "unsaved", "company_id": company_id})
    except Error as error:
        return api_error("E3102", "Could not unsave the listing.", 500)
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None and connection.is_connected():
            connection.close()


@app.route("/update-kept/<int:company_id>", methods=["POST"])
def update_kept(company_id):
    schema.ensure_job_tracking_columns()
    application_status = request.form.get("application_status", "None").strip()
    notes = request.form.get("notes", "").strip()[:5000]
    if application_status not in APPLICATION_STATUSES:
        application_status = "None"

    connection = None
    cursor = None
    try:
        connection = db.connect()
        cursor = connection.cursor()
        cursor.execute(
            "UPDATE companies SET application_status = %s, notes = %s WHERE id = %s AND is_kept = 1",
            (application_status, notes, company_id),
        )
        connection.commit()
    except Error as error:
        print("Could not update saved result.")
        print(error)
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None and connection.is_connected():
            connection.close()
    # The #job anchor reopens this card on the Dashboard, where saved jobs start collapsed.
    return redirect((request.referrer or "/dashboard") + f"#job-{company_id}")


@app.route("/delete-kept/<int:company_id>", methods=["POST"])
def delete_kept(company_id):
    ensure_keep_column()
    connection = None
    cursor = None

    try:
        connection = db.connect()
        cursor = connection.cursor()
        cursor.execute(
            "DELETE FROM companies WHERE id = %s AND is_kept = 1",
            (company_id,),
        )
        connection.commit()
    except Error as error:
        print()
        print("Could not delete saved result.")
        print(error)
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None and connection.is_connected():
            connection.close()

    return redirect("/dashboard")
