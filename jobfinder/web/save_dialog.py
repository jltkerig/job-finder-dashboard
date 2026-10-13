"""A real "Save As" window for files Job Finder holds, shown by this app on your own computer.

A web page cannot make Firefox ask where to save a file (Firefox saves to the Desktop, or opens a PDF in its own
viewer). Job Finder runs on your computer, so it shows the system's Save As window itself and copies the file there.
"""

import os
import subprocess
import sys
from pathlib import Path

# Runs in its own process so a window is never opened inside the web server's threads.
_SCRIPT = """
import sys
import tkinter
from tkinter import filedialog

root = tkinter.Tk()
root.withdraw()
root.attributes("-topmost", True)
path = filedialog.asksaveasfilename(parent=root, title="Save as", initialfile=sys.argv[1], initialdir=sys.argv[2],
                                    defaultextension=".pdf", filetypes=[("PDF", "*.pdf")])
sys.stdout.write(path or "")
"""


def available():
    """True where the Save As window can be shown (Windows with Python's tkinter)."""
    if os.name != "nt":
        return False
    try:
        import tkinter  # noqa: F401
    except ImportError:
        return False
    return True


def ask_save_path(name, start_dir):
    """The path chosen in the Save As window, or None when it was cancelled."""
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    result = subprocess.run([sys.executable, "-c", _SCRIPT, name, str(start_dir)], capture_output=True, text=True,
                            timeout=900, creationflags=flags)
    if result.returncode != 0:
        raise OSError((result.stderr or "the Save As window could not open").strip().splitlines()[-1])
    chosen = result.stdout.strip()
    return Path(chosen) if chosen else None
