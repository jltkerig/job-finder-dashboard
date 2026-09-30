"""Recognise job-board pages (UltiPro, Workday, Oracle, iCIMS, Greenhouse, Lever, Ashby, BambooHR, SmartRecruiters).

identify(url) turns a page address into the settings employer_jobs needs to search that employer's whole
board, or None when the address is not one of these systems.
"""
import re
from urllib.parse import urlparse

_LOCALE = re.compile(r"^[a-z]{2}(?:-[A-Za-z]{2})?$")
_NOT_A_BOARD = {"embed", "api", "v1", "static", "assets", "login", "www", "help", "support", "developers"}


def _segments(path):
    return [segment for segment in path.split("/") if segment]


def identify(url):
    """Settings (without a display name) for the job board this address belongs to, or None."""
    parsed = urlparse(str(url or ""))
    host = (parsed.hostname or "").casefold()
    parts = _segments(parsed.path)
    if not host or parsed.scheme not in ("http", "https"):
        return None

    if re.fullmatch(r"recruiting\d*\.ultipro\.com", host) and len(parts) >= 3 and parts[1].casefold() == "jobboard":
        return {"system": "ultipro", "host": host, "tenant": parts[0], "board": parts[2]}

    match = re.fullmatch(r"([a-z0-9-]+)\.(wd\d+)\.myworkdayjobs\.com", host)
    if match:
        site = next((part for part in parts if not _LOCALE.match(part) and part.casefold() not in {"wday", "login", "job", "details"}), None)
        return {"system": "workday", "host": host, "tenant": match.group(1), "site": site} if site else None

    if re.fullmatch(r"[a-z0-9-]+\.fa(?:\.[a-z0-9-]+)?\.oraclecloud\.com", host):
        match = re.search(r"/hcmUI/CandidateExperience/[^/]+/sites/([^/?#]+)", parsed.path)
        return {"system": "oracle", "host": host, "site": match.group(1)} if match else None

    if host.endswith(".icims.com") and not host.startswith(("employees-", "referral-", "www.")):
        return {"system": "icims", "host": host}

    if host in ("boards.greenhouse.io", "job-boards.greenhouse.io", "boards.eu.greenhouse.io", "job-boards.eu.greenhouse.io"):
        return {"system": "greenhouse", "slug": parts[0]} if parts and parts[0].casefold() not in _NOT_A_BOARD else None

    if host in ("jobs.lever.co", "jobs.eu.lever.co"):
        if not parts or parts[0].casefold() in _NOT_A_BOARD:
            return None
        return {"system": "lever", "slug": parts[0], "eu": host == "jobs.eu.lever.co"}

    if host == "jobs.ashbyhq.com":
        return {"system": "ashby", "slug": parts[0]} if parts and parts[0].casefold() not in _NOT_A_BOARD else None

    match = re.fullmatch(r"([a-z0-9-]+)\.bamboohr\.com", host)
    if match and match.group(1) not in _NOT_A_BOARD:
        return {"system": "bamboohr", "slug": match.group(1)}

    if host == "workforcenow.adp.com" and "recruitment" in parsed.path.casefold():
        query = dict(part.split("=", 1) for part in parsed.query.split("&") if "=" in part)
        if query.get("cid"):
            return {"system": "adp", "host": host, "cid": query["cid"], "ccId": query.get("ccId") or "19000101_000001"}
        return None

    if host == "recruiting.paylocity.com":
        match = re.search(r"/jobs/[A-Za-z]+/([0-9a-f]{8}-[0-9a-f-]{27})", parsed.path, re.I)
        return {"system": "paylocity", "guid": match.group(1).lower()} if match else None

    if host == "apply.workable.com":
        return {"system": "workable", "slug": parts[0]} if parts and parts[0].casefold() not in _NOT_A_BOARD | {"j", "j"} else None

    if host in ("jobs.smartrecruiters.com", "careers.smartrecruiters.com"):
        return {"system": "smartrecruiters", "company": parts[0]} if parts and not parts[0].isdigit() and parts[0].casefold() not in _NOT_A_BOARD else None

    return None


# Hiring platforms Job Finder recognises but cannot read yet (their job lists need a browser).
UNREADABLE_SYSTEMS = {"dayforce": "Dayforce", "paycom": "Paycom", "paycor": "Paycor", "jobvite": "Jobvite",
                      "taleo": "Taleo", "jazzhr": "JazzHR"}


def identify_unreadable(url):
    """The platform name for a hiring board Job Finder recognises but cannot search yet, or None."""
    parsed = urlparse(str(url or ""))
    host = (parsed.hostname or "").casefold()
    parts = _segments(parsed.path)
    if host == "jobs.dayforcehcm.com" and parts:
        client = next((part for part in parts if not _LOCALE.match(part) and part.upper() != "CANDIDATEPORTAL"), None)
        return {"system": "dayforce", "client": client} if client else None
    if host.endswith("paycomonline.net") and "/ats/" in parsed.path:
        return {"system": "paycom", "host": host}
    if host.endswith("recruitingbypaycor.com") or (host.endswith("paycor.com") and "career" in parsed.path.casefold()):
        return {"system": "paycor", "host": host}
    if host == "jobs.jobvite.com" and parts:
        return {"system": "jobvite", "company": parts[0]}
    if host.endswith(".taleo.net"):
        return {"system": "taleo", "host": host}
    if host.endswith(".applytojob.com"):
        return {"system": "jazzhr", "host": host}
    return None


def pretty_name(config):
    """A readable company name from a board's address parts, used when nothing better is known."""
    raw = (config.get("slug") or config.get("company") or config.get("tenant") or "")
    if config.get("system") == "icims":
        raw = re.sub(r"^(?:careers|jobs|career)-", "", config["host"].split(".")[0])
    if config.get("system") == "ultipro":
        raw = ""  # UltiPro tenants are codes such as WAD1002WADM
    words = re.sub(r"[-_]+", " ", raw).strip()
    return words.title() if words else config.get("host", "Job board")
