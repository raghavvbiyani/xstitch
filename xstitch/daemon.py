"""Background daemon for periodic auto-snapshots.

Runs as a background process, periodically checking for significant
changes and auto-snapshotting when detected. Uses a PID file for
lifecycle management.
"""

from __future__ import annotations

import json
import os
import signal
import sys
import time
from pathlib import Path

PID_DIR = Path.home() / ".stitch" / "daemons"


def _pid_file(project_path: str) -> Path:
    safe_name = project_path.replace("/", "_").replace("\\", "_").strip("_")
    return PID_DIR / f"{safe_name}.pid"


def _meta_file(project_path: str) -> Path:
    safe_name = project_path.replace("/", "_").replace("\\", "_").strip("_")
    return PID_DIR / f"{safe_name}.json"


def start_daemon(project_path: str, interval: int = 300):
    """Start a background daemon that auto-snapshots every `interval` seconds."""
    PID_DIR.mkdir(parents=True, exist_ok=True)
    pid_file = _pid_file(project_path)

    # Check if already running
    if pid_file.exists():
        old_pid = int(pid_file.read_text().strip())
        try:
            os.kill(old_pid, 0)
            print(f"Daemon already running (PID {old_pid}). Use 'stitch daemon stop' first.")
            return
        except OSError:
            pid_file.unlink()

    # Fork to background
    pid = os.fork()
    if pid > 0:
        # Parent
        pid_file.write_text(str(pid))
        meta = {"project": project_path, "interval": interval, "pid": pid}
        _meta_file(project_path).write_text(json.dumps(meta, indent=2))
        print(f"Daemon started (PID {pid}), snapshotting every {interval}s.")
        print(f"Stop with: stitch daemon stop")
        return

    # Child — become a daemon
    os.setsid()
    sys.stdin.close()

    # Lazy imports to avoid loading heavy modules in the parent
    from .store import Store
    from .capture import capture_snapshot, has_significant_changes

    store = Store(project_path)
    active_task_id = store.get_active_task_id()

    def _shutdown(signum, frame):
        pid_file.unlink(missing_ok=True)
        _meta_file(project_path).unlink(missing_ok=True)
        sys.exit(0)

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    while True:
        try:
            active_task_id = store.get_active_task_id()
            if active_task_id and has_significant_changes(project_path):
                snap = capture_snapshot(
                    message="",
                    source="daemon",
                    cwd=project_path,
                    task_id=active_task_id,
                )
                rejection = store.add_snapshot(active_task_id, snap)
                if not rejection:
                    store.update_context_file(active_task_id)
        except Exception:
            pass
        time.sleep(interval)


def stop_daemon(project_path: str):
    pid_file = _pid_file(project_path)
    if not pid_file.exists():
        print("No daemon running for this project.")
        return

    pid = int(pid_file.read_text().strip())
    try:
        os.kill(pid, signal.SIGTERM)
        print(f"Daemon stopped (PID {pid}).")
    except OSError:
        print(f"Daemon process {pid} not found (stale PID file).")

    pid_file.unlink(missing_ok=True)
    _meta_file(project_path).unlink(missing_ok=True)


def daemon_status(project_path: str):
    pid_file = _pid_file(project_path)
    meta_file = _meta_file(project_path)

    if not pid_file.exists():
        print("No daemon running for this project.")
        return

    pid = int(pid_file.read_text().strip())
    try:
        os.kill(pid, 0)
        running = True
    except OSError:
        running = False

    if meta_file.exists():
        meta = json.loads(meta_file.read_text())
        interval = meta.get("interval", "?")
    else:
        interval = "?"

    if running:
        print(f"Daemon running (PID {pid}), interval: {interval}s")
    else:
        print(f"Daemon not running (stale PID file for {pid})")
        pid_file.unlink(missing_ok=True)
