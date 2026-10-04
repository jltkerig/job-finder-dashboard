"""Add or check the Resume Builder connector in Claude Desktop's settings.

    python connector.py install   # adds it (backs up the settings file first)
    python connector.py status
"""
import json
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

import config

SERVER_NAME = "resume-builder"


def config_paths():
    """Store (MSIX) installs keep settings inside the package folder; the regular installer uses %APPDATA%."""
    local = Path(os.environ.get("LOCALAPPDATA", ""))
    paths = sorted((local / "Packages").glob("Claude_*/LocalCache/Roaming/Claude/claude_desktop_config.json"))
    paths.append(Path(os.environ.get("APPDATA", "")) / "Claude" / "claude_desktop_config.json")
    return [p for p in paths if p.parent.exists()]


def _read(path):
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8-sig") or "{}")


def server_entry():
    return {"command": sys.executable, "args": [str(config.APP_DIR / "mcp_server.py")]}


def status():
    for path in config_paths():
        try:
            entry = _read(path).get("mcpServers", {}).get(SERVER_NAME)
        except ValueError:
            continue
        if entry:
            return {"installed": True, "path": str(path), "entry": entry}
    return {"installed": False, "path": str(config_paths()[0]) if config_paths() else ""}


def install():
    paths = config_paths()
    if not paths:
        raise SystemExit("Claude Desktop settings folder not found. Open Claude Desktop once, then try again.")
    path = paths[0]
    data = _read(path)  # raises on a broken file instead of overwriting it
    if path.exists():
        backup = path.with_name(f"claude_desktop_config.backup-{datetime.now():%Y%m%d-%H%M%S}.json")
        shutil.copy2(path, backup)
        print(f"Backed up settings to {backup}")
    data.setdefault("mcpServers", {})[SERVER_NAME] = server_entry()
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    print(f"Added '{SERVER_NAME}' to {path}")
    print("Quit Claude Desktop completely (system tray > Quit) and open it again to load the connector.")


if __name__ == "__main__":
    command = sys.argv[1] if len(sys.argv) > 1 else "status"
    if command == "install":
        install()
    else:
        print(json.dumps(status(), indent=2))
