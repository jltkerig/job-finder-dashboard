"""Blocked domains and companies: reading and changing the lists, and the Settings page forms that do
it.
"""

from datetime import datetime
import json
import re

from flask import abort, jsonify, redirect, request
from mysql.connector import Error

from jobfinder import db
from jobfinder.web import schema
from jobfinder.web.core import api_error, app, log_error_code
from jobfinder.web.webfiles import (
    BLOCKED_COMPANIES_FILE,
    BLOCKED_DOMAINS_FILE,
    BLOCK_METADATA_FILE,
    RECOMMENDED_DOMAINS_FILE,
)


def read_block_metadata():
    try:
        data = json.loads(BLOCK_METADATA_FILE.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return {"domains": data.get("domains", {}), "companies": data.get("companies", {})}
    except (OSError, ValueError):
        pass
    return {"domains": {}, "companies": {}}


def save_block_metadata(kind, key, source=None):
    data = read_block_metadata()
    if source is None:
        data[kind].pop(key, None)
    elif key not in data[kind]:
        data[kind][key] = {"source": source, "blocked_at": datetime.now().astimezone().isoformat(timespec="seconds")}
    temp = BLOCK_METADATA_FILE.with_suffix(".tmp")
    temp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    temp.replace(BLOCK_METADATA_FILE)


def block_details(kind, names):
    data = read_block_metadata()[kind]
    recommended = set()
    if kind == "domains" and RECOMMENDED_DOMAINS_FILE.exists():
        recommended = {line.strip().lower() for line in RECOMMENDED_DOMAINS_FILE.read_text(encoding="utf-8").splitlines() if line.strip() and not line.startswith("#")}
    return {name: {"source": data.get(name.casefold(), {}).get("source") or ("Recommended" if name.lower() in recommended else "User"),
                   "blocked_at": data.get(name.casefold(), {}).get("blocked_at")}
            for name in names}


def add_domain_to_blocklist(domain):
    domain = (domain or "").strip().lower().removeprefix("www.")
    if not domain:
        return

    existing = set()
    if BLOCKED_DOMAINS_FILE.exists():
        existing = {
            line.strip().lower()
            for line in BLOCKED_DOMAINS_FILE.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.strip().startswith("#")
        }

    if domain in existing:
        return

    with BLOCKED_DOMAINS_FILE.open("a", encoding="utf-8") as file:
        if BLOCKED_DOMAINS_FILE.stat().st_size:
            file.write("\n")
        file.write(domain + "\n")
    save_block_metadata("domains", domain, "User")


def get_blocked_companies():
    if not BLOCKED_COMPANIES_FILE.exists():
        return []
    return sorted({line.strip() for line in BLOCKED_COMPANIES_FILE.read_text(encoding="utf-8").splitlines()
                   if line.strip() and not line.lstrip().startswith("#")}, key=str.casefold)


def add_blocked_company(name):
    name = " ".join((name or "").split())[:150]
    if not name or "\n" in name or "\r" in name:
        return False
    if name.casefold() not in {item.casefold() for item in get_blocked_companies()}:
        with BLOCKED_COMPANIES_FILE.open("a", encoding="utf-8") as file:
            file.write(name + "\n")
        save_block_metadata("companies", name.casefold(), "User")
    return True


@app.route("/settings/blocked-companies", methods=["POST"])
def manage_blocked_company():
    name = " ".join(request.form.get("company", "").split())[:150]
    action = request.form.get("action", "")
    if not name:
        return redirect("/rejected-listings?company_notice=invalid#blocked-companies")
    if action == "add":
        add_blocked_company(name)
    elif action == "remove":
        names = [item for item in get_blocked_companies() if item.casefold() != name.casefold()]
        BLOCKED_COMPANIES_FILE.write_text("\n".join(names) + ("\n" if names else ""), encoding="utf-8")
        save_block_metadata("companies", name.casefold())
    else:
        abort(400)
    return redirect("/rejected-listings?company_notice=" + action + "#blocked-companies")


@app.route("/block-company/<int:company_id>", methods=["POST"])
def block_company(company_id):
    schema.ensure_job_tracking_columns()
    connection = None
    cursor = None
    try:
        connection = db.connect()
        cursor = connection.cursor(dictionary=True)
        cursor.execute("SELECT name FROM companies WHERE id = %s", (company_id,))
        row = cursor.fetchone()
        if not row or not add_blocked_company(row.get("name")):
            return api_error("E3220", "No company name was available to block.", 404)
        return jsonify({"status": "blocked", "company": row["name"]})
    except Error as error:
        log_error_code("E3221", f"Could not block company: {error}")
        return api_error("E3221", "Could not block this company.", 500)
    finally:
        if cursor is not None: cursor.close()
        if connection is not None and connection.is_connected(): connection.close()


def get_blocked_domains():
    if not BLOCKED_DOMAINS_FILE.exists():
        return []
    return sorted({line.strip().lower() for line in BLOCKED_DOMAINS_FILE.read_text(encoding="utf-8").splitlines()
                   if line.strip() and not line.lstrip().startswith("#")})


@app.route("/settings/blocked-domains", methods=["POST"])
def manage_blocked_domain():
    domain = request.form.get("domain", "").strip().lower().removeprefix("www.")
    action = request.form.get("action", "")
    if not re.fullmatch(r"(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}", domain):
        return redirect("/rejected-listings?domain_notice=invalid#blocked-domains")
    if action == "add":
        add_domain_to_blocklist(domain)
    elif action == "remove":
        remove_domain_from_blocklist(domain)
    else:
        abort(400)
    return redirect("/rejected-listings?domain_notice=" + action + "#blocked-domains")


def remove_domain_from_blocklist(domain):
    domain = (domain or "").strip().lower().removeprefix("www.")
    if not domain or not BLOCKED_DOMAINS_FILE.exists():
        return

    lines = BLOCKED_DOMAINS_FILE.read_text(encoding="utf-8").splitlines()
    kept = []
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and stripped.lower() == domain:
            continue
        kept.append(line)

    BLOCKED_DOMAINS_FILE.write_text("\n".join(kept).rstrip() + "\n", encoding="utf-8")
    save_block_metadata("domains", domain)
