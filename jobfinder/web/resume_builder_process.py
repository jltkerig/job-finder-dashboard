"""Keeps Résumé Builder running for as long as Job Finder is: it starts with Job Finder, and starts again if it stops.

Résumé Builder is its own small web app (resume-builder/app.py, port 5001) that the nav bar links to. Job Finder starts
it quietly (no window, no browser tab) so the link always works. Where the resume-builder folder is not there (the
shipped copies of Job Finder don't include it) nothing happens."""

import os
import socket
import subprocess
import sys
import threading
from pathlib import Path

PORT = 5001
CHECK_SECONDS = 20
FOLDER = Path(__file__).resolve().parents[2] / "resume-builder"

_started = None  # the process this module started, if it did
_lock = threading.Lock()


def is_running(port=PORT):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return True
    except OSError:
        return False


def start_if_needed(folder=FOLDER, port=PORT):
    """Starts Résumé Builder when nothing is answering on its port. Returns True if a new copy was started."""
    global _started
    if not (Path(folder) / "app.py").is_file() or is_running(port):
        return False
    with _lock:
        if _started is not None and _started.poll() is None:
            return False  # starting up: not answering yet, but it is on its way
        try:
            out = open(Path(folder) / "resume-builder.log", "ab")
            err = open(Path(folder) / "resume-builder-error.log", "ab")
        except OSError:
            return False
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        try:
            _started = subprocess.Popen([sys.executable, "app.py"], cwd=str(folder), stdout=out, stderr=err,
                                        stdin=subprocess.DEVNULL, creationflags=flags)
        except OSError:
            return False
        return True


def keep_running(folder=FOLDER, port=PORT, seconds=CHECK_SECONDS):
    """Start Résumé Builder now and look every few seconds that it is still up. Runs in a background thread."""
    def watch():
        stop = threading.Event()
        while not stop.is_set():
            try:
                start_if_needed(folder, port)
            except Exception:
                pass  # never let the watcher stop Job Finder
            stop.wait(seconds)

    thread = threading.Thread(target=watch, name="resume-builder-watch", daemon=True)
    thread.start()
    return thread
