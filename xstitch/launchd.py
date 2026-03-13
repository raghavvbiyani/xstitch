"""macOS launchd integration for Stitch daemon auto-start on login.

Creates a LaunchAgent plist so the Stitch daemon survives reboots.
On login, launchd starts a lightweight watcher that monitors
projects with active Stitch tasks and auto-snapshots them.
"""

from __future__ import annotations

import json
import os
import plistlib
import subprocess
from pathlib import Path

PLIST_LABEL = "com.stitch.daemon"
LAUNCH_AGENTS_DIR = Path.home() / "Library" / "LaunchAgents"
PLIST_PATH = LAUNCH_AGENTS_DIR / f"{PLIST_LABEL}.plist"
GLOBAL_HOME = Path.home() / ".stitch"
WATCHER_SCRIPT = GLOBAL_HOME / "stitch_watcher.sh"


def install_launchd(interval: int = 600):
    """Install a macOS LaunchAgent that runs the Stitch watcher on login."""
    LAUNCH_AGENTS_DIR.mkdir(parents=True, exist_ok=True)
    GLOBAL_HOME.mkdir(parents=True, exist_ok=True)

    _write_watcher_script()

    plist = {
        "Label": PLIST_LABEL,
        "ProgramArguments": ["/bin/bash", str(WATCHER_SCRIPT)],
        "StartInterval": interval,
        "RunAtLoad": True,
        "StandardOutPath": str(GLOBAL_HOME / "daemon.log"),
        "StandardErrorPath": str(GLOBAL_HOME / "daemon_err.log"),
        "EnvironmentVariables": {
            "PATH": "/usr/local/bin:/usr/bin:/bin:/opt/homebrew/bin:"
                    + os.environ.get("PATH", ""),
        },
    }

    with open(PLIST_PATH, "wb") as f:
        plistlib.dump(plist, f)

    # Unload if already loaded, then load
    subprocess.run(
        ["launchctl", "unload", str(PLIST_PATH)],
        capture_output=True,
    )
    result = subprocess.run(
        ["launchctl", "load", str(PLIST_PATH)],
        capture_output=True,
        text=True,
    )

    if result.returncode == 0:
        print(f"LaunchAgent installed: {PLIST_PATH}")
        print(f"Stitch watcher will run every {interval}s and survive reboots.")
        print(f"Logs: {GLOBAL_HOME}/daemon.log")
    else:
        print(f"Failed to load LaunchAgent: {result.stderr}")

    return result.returncode == 0


def uninstall_launchd():
    """Remove the Stitch LaunchAgent."""
    if PLIST_PATH.exists():
        subprocess.run(
            ["launchctl", "unload", str(PLIST_PATH)],
            capture_output=True,
        )
        PLIST_PATH.unlink()
        print(f"LaunchAgent removed: {PLIST_PATH}")
    else:
        print("No LaunchAgent installed.")

    if WATCHER_SCRIPT.exists():
        WATCHER_SCRIPT.unlink()


def launchd_status():
    """Check if the LaunchAgent is loaded."""
    result = subprocess.run(
        ["launchctl", "list", PLIST_LABEL],
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        lines = result.stdout.strip().split("\n")
        print(f"LaunchAgent {PLIST_LABEL}: LOADED")
        for line in lines:
            print(f"  {line}")
    else:
        print(f"LaunchAgent {PLIST_LABEL}: NOT LOADED")
        if PLIST_PATH.exists():
            print(f"  Plist exists at {PLIST_PATH} but is not loaded.")
            print(f"  Run: launchctl load {PLIST_PATH}")


def _write_watcher_script():
    """Write the shell script that launchd executes periodically."""
    python_path = _find_python()

    script = f"""\
#!/bin/bash
# Stitch Watcher — auto-snapshots projects with active tasks.
# Invoked by launchd every N seconds. Lightweight: skips if no changes.

REGISTRY="{GLOBAL_HOME}/registry.json"

if [ ! -f "$REGISTRY" ]; then
    exit 0
fi

# Read all project paths from global registry
PROJECTS=$({python_path} -c "
import json, sys
try:
    r = json.load(open('$REGISTRY'))
    seen = set()
    for t in r.get('tasks', []):
        p = t.get('project_path', '')
        s = t.get('status', '')
        if p and s == 'active' and p not in seen:
            seen.add(p)
            print(p)
except Exception:
    pass
")

for PROJECT in $PROJECTS; do
    if [ -d "$PROJECT/.stitch" ] && [ -f "$PROJECT/.stitch/active_task" ]; then
        cd "$PROJECT"
        # Only snapshot if there are git changes
        if command -v git >/dev/null 2>&1 && git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
            CHANGES=$(git status --short 2>/dev/null | wc -l | tr -d ' ')
            if [ "$CHANGES" -gt "0" ]; then
                {python_path} -m xstitch.cli snap -m "auto-snapshot (launchd watcher)" --source daemon 2>/dev/null
            fi
        fi
    fi
done
"""
    WATCHER_SCRIPT.write_text(script)
    os.chmod(str(WATCHER_SCRIPT), 0o755)


def _find_python() -> str:
    """Find the python3 that has xstitch installed."""
    for candidate in [
        "/usr/local/bin/python3",
        "/opt/homebrew/bin/python3",
        "/usr/bin/python3",
    ]:
        if os.path.exists(candidate):
            return candidate
    return "python3"
