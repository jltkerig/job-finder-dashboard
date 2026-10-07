"""Every job on the Apply queue gets a résumé and cover letter, written by Claude Code in the background.

When a job is queued (and on the daily timer's check, so nothing is missed), Claude Code runs headless (`claude -p`)
on your Claude subscription, with only the Résumé Builder connector's tools. It writes what each queued job is
missing and saves the PDFs to that job, where the Dashboard shows them. No API key; nothing is ever submitted.
Only the running dashboard switches this on (start()), so tests never launch Claude.
"""

import json
import shutil
import subprocess
import sys
import threading
from datetime import date
from pathlib import Path

from jobfinder.web.application_files import RESUME_BUILDER_DIR, files_by_job
from jobfinder.web.core import log_error_code
from jobfinder.web.webfiles import BASE_DIR

LOG_FILE = BASE_DIR / "logs" / "queue-documents.log"
TIMEOUT_SECONDS = 45 * 60
MAX_TRIES_PER_DAY = 2  # per job, so a failing job can't use up your Claude usage

enabled = False
_lock = threading.Lock()
_running = set()  # job ids being written right now
_tries = {}  # {(job id, day): tries}

PROMPT = """You are running unattended for Job Finder: nobody will answer questions or say "update", so start the
work now; this message is the user's request. Write a tailored résumé and
cover letter for each of these Apply queue jobs, only the kinds listed as missing: {jobs}.
Follow the Resume Builder connector's instructions in full: get_writing_rules first, then the profile, the current
résumé and every reference document, then get_job for each job (read the whole listing). Save each with
save_resume / save_cover_letter, always passing the job's job_id. Never invent facts. Do one job at a time."""


def start():
    global enabled
    enabled = True


def missing_documents(queue):
    """[{"job_id", "job_title", "company", "needs": [...]}] for queued jobs without both files."""
    have = {job_id: {f["kind"] for f in files} for job_id, files in files_by_job().items()}
    missing = []
    for job in queue:
        needs = [kind for kind in ("resume", "cover_letter") if kind not in have.get(job["id"], set())]
        if needs:
            missing.append({"job_id": job["id"], "job_title": job.get("career_job_title") or "",
                            "company": job.get("name") or "", "needs": needs})
    return missing


def writing_ids():
    with _lock:
        return set(_running)


def _claude():
    return shutil.which("claude") or str(Path.home() / ".local" / "bin" / "claude.exe")


def _command(jobs):
    config = {"mcpServers": {"resume-builder": {"command": sys.executable,
                                                "args": [str(RESUME_BUILDER_DIR / "mcp_server.py")]}}}
    return [_claude(), "-p", PROMPT.format(jobs=json.dumps(jobs)), "--mcp-config", json.dumps(config),
            "--strict-mcp-config", "--allowedTools", "mcp__resume-builder", "--output-format", "text"]


def _run(jobs):
    ids = {job["job_id"] for job in jobs}
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8") as log:
            log.write(f"\n=== {date.today()} writing for {json.dumps(jobs)}\n")
            log.flush()
            result = subprocess.run(_command(jobs), cwd=RESUME_BUILDER_DIR, stdout=log, stderr=subprocess.STDOUT,
                                    stdin=subprocess.DEVNULL, timeout=TIMEOUT_SECONDS,
                                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if result.returncode:
            log_error_code("E3260", f"Claude Code could not write the queue's résumés (exit {result.returncode}). "
                                    f"See {LOG_FILE}.")
    except (OSError, subprocess.SubprocessError) as error:
        log_error_code("E3260", f"Claude Code could not write the queue's résumés: {error}")
    finally:
        with _lock:
            _running.difference_update(ids)


def start_if_needed():
    """Starts Claude Code for queued jobs missing files, unless it's switched off or already writing them."""
    if not enabled:
        return False
    from jobfinder.web.auto_apply import get_apply_queue

    today = date.today().isoformat()
    with _lock:
        if _running:
            return False  # one run at a time; the next check picks up anything still missing
        jobs = [job for job in missing_documents(get_apply_queue())
                if _tries.get((job["job_id"], today), 0) < MAX_TRIES_PER_DAY]
        if not jobs:
            return False
        for job in jobs:
            _tries[(job["job_id"], today)] = _tries.get((job["job_id"], today), 0) + 1
            _running.add(job["job_id"])
    threading.Thread(target=_run, args=(jobs,), name="queue-documents", daemon=True).start()
    return True
