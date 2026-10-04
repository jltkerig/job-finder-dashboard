"""Build a Job Finder release in one step.

    python release.py              # next patch version (1.1.112 -> 1.1.113)
    python release.py 1.2.0        # a specific version
    python release.py --restart    # also restart the dashboard afterwards (only when no search is running)

Steps: bump APP_VERSION in dashboard.py, run the tests, build
..\\job-finder-zips\\job-finder-dashboard-vX.Y.Z.zip, unpack it to a temporary folder and run the tests
there too. If any step fails, the version bump is undone and no ZIP is left behind.

The ZIP holds every file git knows about (committed or new, not ignored) that still exists, minus
EXCLUDE. So a new file is included without editing this script; personal and machine-only files are
kept out by .gitignore.
"""
import argparse
import fnmatch
import json
import re
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ZIP_DIR = ROOT.parent / "job-finder-zips"
DASHBOARD = ROOT / "dashboard.py"
VERSION_LINE = re.compile(r'^APP_VERSION = "(\d+)\.(\d+)\.(\d+)"$', re.M)
EXCLUDE = [
    ".env", "*.log", "search_skips.jsonl", "block_metadata.json", ".stop-requested", ".update-in-progress",
    "watched_employers.before-*.json", "user-builds/*", "user-data/*", "logs/*", "feed_cache/*", "*.pyc",
    # Not shipped yet: the updater replaces whole folders, which would wipe resume-builder/data.
    "resume-builder/*",
]
DASHBOARD_URL = "http://127.0.0.1:5000"


def current_version():
    match = VERSION_LINE.search(DASHBOARD.read_text(encoding="utf-8"))
    if not match:
        sys.exit("Cannot find APP_VERSION in dashboard.py")
    return tuple(int(part) for part in match.groups())


def set_version(version):
    text = DASHBOARD.read_bytes().decode("utf-8")
    new_text, count = VERSION_LINE.subn(f'APP_VERSION = "{version}"', text)
    if count != 1:
        sys.exit("Could not update APP_VERSION")
    DASHBOARD.write_bytes(new_text.encode("utf-8"))


def run_tests(folder):
    result = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py", "-t", "tests"],
                            cwd=folder, capture_output=True, text=True)
    summary = [line for line in result.stderr.splitlines() if line.startswith(("Ran ", "OK", "FAILED"))]
    print("   ", " / ".join(summary) or "no test summary")
    if result.returncode != 0:
        print(result.stderr[-3000:])
    return result.returncode == 0


def release_files():
    listed = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard"], cwd=ROOT,
                            capture_output=True, text=True, check=True).stdout.splitlines()
    return sorted(name for name in listed
                  if name and (ROOT / name).is_file() and not any(fnmatch.fnmatch(name, pattern) for pattern in EXCLUDE))


def build_zip(version):
    ZIP_DIR.mkdir(exist_ok=True)
    out = ZIP_DIR / f"job-finder-dashboard-v{version}.zip"
    if out.exists():
        sys.exit(f"{out.name} already exists")
    top = f"job-finder-dashboard-v{version}/"
    files = release_files()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        for name in files:
            archive.write(ROOT / name, top + name)  # zip entries always use forward slashes
    with zipfile.ZipFile(out) as archive:
        packed = archive.read(top + "dashboard.py").decode("utf-8")
    if f'APP_VERSION = "{version}"' not in packed:
        out.unlink()
        sys.exit("The ZIP's dashboard.py has the wrong version")
    return out, len(files)


def search_running():
    name = "job_" + "finder.py"  # split so this script's own command line never matches
    processes = subprocess.run(["powershell", "-NoProfile", "-Command",
                                f"(Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
                                f"Where-Object {{ $_.CommandLine -like '*{name}*' }}).Count"],
                               capture_output=True, text=True).stdout.strip()
    if processes not in ("", "0"):
        return True
    try:
        with urllib.request.urlopen(f"{DASHBOARD_URL}/search-status", timeout=5) as response:
            return bool(json.load(response).get("running"))
    except OSError:
        return False  # dashboard not running


def restart(version):
    if search_running():
        print("A search is running, so the dashboard was not restarted. Run start.ps1 when it finishes.")
        return
    # start.ps1 never returns when run from a script, so it is started on its own and the version is polled.
    subprocess.Popen(["powershell", "-ExecutionPolicy", "Bypass", "-File", str(ROOT / "start.ps1")],
                     cwd=ROOT, creationflags=subprocess.CREATE_NEW_CONSOLE)
    for _ in range(90):
        time.sleep(2)
        try:
            with urllib.request.urlopen(f"{DASHBOARD_URL}/", timeout=3) as response:
                if f"Job Finder v{version}" in response.read().decode("utf-8", "replace"):
                    print(f"Dashboard restarted on v{version}.")
                    return
        except OSError:
            continue
    print("The dashboard did not report the new version within 3 minutes. Check startup.log.")


def main():
    parser = argparse.ArgumentParser(description="Build a Job Finder release ZIP.")
    parser.add_argument("version", nargs="?", help="X.Y.Z (default: next patch version)")
    parser.add_argument("--restart", action="store_true", help="restart the dashboard when no search is running")
    args = parser.parse_args()

    old = current_version()
    old_text = ".".join(map(str, old))
    if args.version:
        if not re.fullmatch(r"\d+\.\d+\.\d+", args.version):
            sys.exit("Version must look like 1.2.3")
        version = args.version
    else:
        version = f"{old[0]}.{old[1]}.{old[2] + 1}"
    print(f"1. Version {old_text} -> {version}")
    set_version(version)
    out = None
    try:
        print("2. Tests")
        if not run_tests(ROOT):
            raise SystemExit("Tests failed; nothing released.")
        print("3. ZIP")
        out, count = build_zip(version)
        print(f"    {out} ({count} files)")
        print("4. Tests from the unpacked ZIP")
        with tempfile.TemporaryDirectory() as temp:
            with zipfile.ZipFile(out) as archive:
                archive.extractall(temp)
            if not run_tests(Path(temp) / f"job-finder-dashboard-v{version}"):
                raise SystemExit("Tests failed in the unpacked ZIP; nothing released.")
    except BaseException:
        set_version(old_text)
        if out is not None and out.exists():
            out.unlink()
        print(f"Version put back to {old_text}.")
        raise
    print(f"Released v{version}.")
    if args.restart:
        print("5. Restart")
        restart(version)


if __name__ == "__main__":
    main()
