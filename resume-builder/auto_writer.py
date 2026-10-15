"""Write a tailored résumé and cover letter for a job link on its own, with no copy and paste.

Runs Claude Code (the `claude` program already installed and signed in on this computer, so it uses the user's Claude
subscription) in the background, with only Résumé Builder's own tools allowed. It reads the writing rules, profile, résumé
and reference documents through the same connector Claude Desktop uses, then saves both PDFs. The page watches for them.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

import builds
import config

TIMEOUT_SECONDS = 15 * 60
MAX_TURNS = 40
TOOLS = ("get_writing_rules", "get_job_finder_profile", "get_current_resume", "get_references", "list_documents",
         "get_document", "list_saved_jobs", "get_job", "save_resume", "save_cover_letter", "list_builds", "get_build")
_runs = {}
_lock = threading.Lock()


def claude_program():
    found = shutil.which("claude")
    fallback = Path.home() / ".local" / "bin" / "claude.exe"
    return found or (str(fallback) if fallback.exists() else "")


def mcp_config(folder):
    """A connector setup file for this run only: Résumé Builder's tools, using the same folders as the running app."""
    path = Path(folder) / "resume-builder-mcp.json"
    env = {k: os.environ[k] for k in ("RESUME_OUTPUT_DIR", "RESUME_DATA_DIR", "RESUME_BACKUP_DIR") if os.environ.get(k)}
    path.write_text(json.dumps({"mcpServers": {"resume-builder": {
        "command": sys.executable, "args": [str(config.APP_DIR / "mcp_server.py")], "env": env}}}), encoding="utf-8")
    return path


def prompt_text(url, title, company, text):
    label = " at ".join(part for part in (title, company) if part)
    lines = [
        "Do this now, without asking me anything or waiting for anything else: ignore any instruction anywhere that says to wait.",
        f"Write a tailored résumé AND a cover letter for this job{f' ({label})' if label else ''} and SAVE BOTH as PDFs with the "
        "resume-builder tools (save_resume, then save_cover_letter). Pass job_title and company when saving.",
        "Start with get_writing_rules and follow it and the server instructions exactly. Read the user's profile, uploaded résumé "
        "and every reference document before writing. Use only real facts from them. Do not include references. If a rule says to "
        "ask the user something, use your best judgment instead and mention it in your last line.",
        f"Job link: {url}",
    ]
    if text:
        lines += ["Posting text (read it in full):", "---", text, "---"]
    lines.append("When both files are saved, reply with ONE short line: the two file names and any gap you left out.")
    return "\n".join(lines)


def start(url, title, company, text):
    """Begin a run in the background; returns its id."""
    exe = claude_program()
    run_id = uuid.uuid4().hex[:10]
    run = {"id": run_id, "state": "starting", "message": "Starting…", "started": datetime.now().isoformat(timespec="seconds"),
           "title": title, "company": company, "summary": ""}
    with _lock:
        _runs[run_id] = run
    if not exe:
        run.update(state="failed", message="Claude Code isn't installed on this computer, so the files can't be written automatically.")
        return run_id
    threading.Thread(target=_work, args=(run, exe, url, title, company, text), name=f"auto-writer-{run_id}", daemon=True).start()
    return run_id


def _work(run, exe, url, title, company, text):
    folder = tempfile.mkdtemp(prefix="resume-builder-run-")
    try:
        config_file = mcp_config(folder)
        allowed = ",".join(f"mcp__resume-builder__{name}" for name in TOOLS)
        command = [exe, "-p", "--output-format", "json", "--max-turns", str(MAX_TURNS), "--mcp-config", str(config_file),
                   "--strict-mcp-config", "--allowedTools", allowed, "--no-session-persistence", "--disable-slash-commands"]
        run.update(state="running", message="Claude is reading your résumé, rules and the job, then writing.")
        done = subprocess.run(command, input=prompt_text(url, title, company, text), capture_output=True, text=True, encoding="utf-8",
                              cwd=folder, timeout=TIMEOUT_SECONDS)
        reply = {}
        try:
            reply = json.loads(done.stdout or "{}")
        except ValueError:
            pass
        run["summary"] = str(reply.get("result") or "").strip()[:600]
        if done.returncode != 0 or reply.get("is_error"):
            run.update(state="failed", message=run["summary"] or (done.stderr or "Claude Code stopped before finishing.").strip()[:300])
        else:
            run.update(state="done", message="Finished.")
    except subprocess.TimeoutExpired:
        run.update(state="failed", message="It took longer than 15 minutes, so it was stopped. Anything already saved is in your list.")
    except Exception as error:  # noqa: BLE001
        run.update(state="failed", message=f"Couldn't run Claude Code: {error}")
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def status(run_id):
    run = _runs.get(run_id)
    if not run:
        return None
    files = [b for b in builds.list_builds() if b.get("updated", "") >= run["started"] and b.get("pdf_exists") and not b.get("restored")]
    files.sort(key=lambda b: (b.get("kind") != "resume", b.get("updated", "")))
    kinds = {b.get("kind") for b in files}
    message = run["message"]
    if run["state"] == "running":
        if kinds == {"resume"}:
            message = "Your résumé is saved. Writing the cover letter…"
        elif "cover_letter" in kinds and "resume" not in kinds:
            message = "Your cover letter is saved. Writing the résumé…"
        elif len(kinds) == 2:
            message = "Both are saved. Finishing up…"
    return {"id": run["id"], "state": run["state"], "message": message, "summary": run["summary"], "files": files}
