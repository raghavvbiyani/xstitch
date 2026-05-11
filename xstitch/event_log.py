"""Global append-only event log for cross-agent context sync.

Every mutation (task create, update, snapshot, decision, context/handoff
regeneration, orphan move) appends one JSON line to
``~/.ahcp/events.jsonl``. Agents can call :func:`read_events` or the
``stitch_what_changed`` MCP tool to discover what other agents have done
since they last checked in.

Why JSONL + file lock instead of a DB:
    * Zero dependencies, matches the rest of Stitch's storage style.
    * Append-only semantics play nicely with ``fcntl.flock`` and are
      robust to crashes (worst case = one garbled line, skipped by reader).
    * Small enough that linear reads are fine at the volumes we expect
      (thousands of events per developer per month).

The event schema is intentionally simple:

.. code-block:: json

    {
      "ts": "2026-04-20T07:14:18.123456+00:00",
      "seq": 4217,
      "event_type": "task_updated",
      "task_id": "1d6a4773d4f7",
      "project_path": "/path/to/project",
      "project_key": "project-abc12345",
      "agent": "cursor",
      "meta": {"tool": "stitch_update_task"}
    }

Rotation: when ``events.jsonl`` grows past ``EVENT_LOG_ROTATE_BYTES`` the
file is renamed to ``events-YYYY-MM-DD-NNN.jsonl`` and a fresh file is
started. Readers can pass ``include_archives=True`` to walk rotated files
too (used mainly by the doctor/inspector tools).
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

from .locks import FileLockTimeout, file_lock

# Event types emitted by Store + repair tooling.
EVENT_TASK_CREATED = "task_created"
EVENT_TASK_UPDATED = "task_updated"
EVENT_SNAPSHOT_ADDED = "snapshot_added"
EVENT_DECISION_ADDED = "decision_added"
EVENT_CONTEXT_UPDATED = "context_updated"
EVENT_HANDOFF_BUILT = "handoff_built"
EVENT_TASK_MOVED = "task_moved"

ALL_EVENT_TYPES = frozenset(
    {
        EVENT_TASK_CREATED,
        EVENT_TASK_UPDATED,
        EVENT_SNAPSHOT_ADDED,
        EVENT_DECISION_ADDED,
        EVENT_CONTEXT_UPDATED,
        EVENT_HANDOFF_BUILT,
        EVENT_TASK_MOVED,
    }
)

# Rotate after 10 MB by default; override via env for tests.
EVENT_LOG_ROTATE_BYTES = int(os.environ.get("AHCP_EVENT_LOG_ROTATE_BYTES", str(10 * 1024 * 1024)))


def _events_file() -> Path:
    """Current active events log path. Late-imports GLOBAL_HOME so tests
    that monkeypatch the global home are respected."""
    from .store import GLOBAL_HOME
    return GLOBAL_HOME / "events.jsonl"


def _seq_file() -> Path:
    from .store import GLOBAL_HOME
    return GLOBAL_HOME / ".event_seq"


def _cursors_dir() -> Path:
    from .store import GLOBAL_HOME
    return GLOBAL_HOME / "cursors"


def _lock_path(name: str) -> Path:
    from .store import GLOBAL_HOME
    return GLOBAL_HOME / f".{name}.lock"


def _iso_now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="microseconds")


def _next_seq() -> int:
    """Monotonic per-host sequence number. Uses a small lockfile so two
    processes can never hand out the same seq."""
    seq_file = _seq_file()
    lock = _lock_path("event_seq")
    try:
        with file_lock(lock, timeout=3.0):
            try:
                current = int(seq_file.read_text().strip()) if seq_file.exists() else 0
            except (ValueError, OSError):
                current = 0
            nxt = current + 1
            try:
                seq_file.write_text(str(nxt))
            except OSError:
                pass
            return nxt
    except FileLockTimeout:
        # Fall back to a time-based seq. Not perfectly monotonic under
        # heavy contention but only used as a tiebreaker for readers.
        return int(_dt.datetime.now(_dt.timezone.utc).timestamp() * 1_000_000)


# ---------------------------------------------------------------------------
# Writer
# ---------------------------------------------------------------------------


def append_event(
    event_type: str,
    task_id: str,
    project_path: str,
    meta: Optional[dict] = None,
    agent: Optional[str] = None,
) -> Optional[dict]:
    """Append one event to the global log.

    Returns the written event dict on success, ``None`` on failure. Never
    raises — the event log is a best-effort sidecar and must not break
    the main write path of the caller.
    """
    if event_type not in ALL_EVENT_TYPES:
        # Allow unknown types so future extensions don't need to bump
        # this module; warn to stderr to surface typos.
        print(
            f"  [Stitch WARNING] Unknown event_type={event_type!r}; accepting anyway",
            file=sys.stderr,
        )

    try:
        from .store import project_key  # local import to avoid circular
        pkey = project_key(Path(project_path)) if project_path else ""
    except Exception:
        pkey = ""

    event = {
        "ts": _iso_now(),
        "seq": _next_seq(),
        "event_type": event_type,
        "task_id": task_id,
        "project_path": project_path or "",
        "project_key": pkey,
        "agent": agent or os.environ.get("AHCP_AGENT", "unknown"),
        "meta": meta or {},
    }

    events_file = _events_file()
    lock = _lock_path("events")
    try:
        with file_lock(lock, timeout=3.0):
            events_file.parent.mkdir(parents=True, exist_ok=True)
            with events_file.open("a", encoding="utf-8") as f:
                f.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")
        _rotate_if_needed()
        return event
    except (FileLockTimeout, OSError) as e:
        print(f"  [Stitch WARNING] Could not append event {event_type}: {e}", file=sys.stderr)
        return None


def _rotate_if_needed() -> None:
    events_file = _events_file()
    if not events_file.exists():
        return
    try:
        size = events_file.stat().st_size
    except OSError:
        return
    if size < EVENT_LOG_ROTATE_BYTES:
        return

    today = _dt.datetime.now(_dt.timezone.utc).date().isoformat()
    base = events_file.parent
    i = 1
    while True:
        archive = base / f"events-{today}-{i:03d}.jsonl"
        if not archive.exists():
            break
        i += 1
    try:
        events_file.rename(archive)
    except OSError as e:
        print(f"  [Stitch WARNING] Could not rotate event log: {e}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Reader
# ---------------------------------------------------------------------------


@dataclass
class EventFilter:
    since_ts: Optional[str] = None
    since_seq: Optional[int] = None
    project_path: Optional[str] = None
    project_key: Optional[str] = None
    task_id: Optional[str] = None
    agent: Optional[str] = None
    event_types: Optional[Iterable[str]] = None

    def matches(self, event: dict) -> bool:
        if self.since_ts and event.get("ts", "") <= self.since_ts:
            return False
        if self.since_seq is not None and event.get("seq", 0) <= self.since_seq:
            return False
        if self.project_path and event.get("project_path") != self.project_path:
            return False
        if self.project_key and event.get("project_key") != self.project_key:
            return False
        if self.task_id and event.get("task_id") != self.task_id:
            return False
        if self.agent and event.get("agent") != self.agent:
            return False
        if self.event_types is not None and event.get("event_type") not in set(self.event_types):
            return False
        return True


def read_events(
    filt: Optional[EventFilter] = None,
    limit: int = 500,
    include_archives: bool = False,
) -> list[dict]:
    """Read events from the log, filtered and bounded.

    Corrupted lines are skipped with a warning rather than failing the
    entire read. Results are returned oldest-first, sorted by ``(ts, seq)``.
    """
    filt = filt or EventFilter()
    events_file = _events_file()
    files: list[Path] = []
    if include_archives and events_file.parent.exists():
        files.extend(sorted(events_file.parent.glob("events-*.jsonl")))
    if events_file.exists():
        files.append(events_file)

    results: list[dict] = []
    for fp in files:
        try:
            with fp.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        ev = json.loads(line)
                    except json.JSONDecodeError:
                        print(
                            f"  [Stitch WARNING] Skipped corrupted event line in {fp.name}",
                            file=sys.stderr,
                        )
                        continue
                    if filt.matches(ev):
                        results.append(ev)
        except OSError as e:
            print(f"  [Stitch WARNING] Could not read {fp}: {e}", file=sys.stderr)

    results.sort(key=lambda e: (e.get("ts", ""), e.get("seq", 0)))
    if limit and limit > 0:
        # Keep the *most recent* ``limit`` events (tail).
        results = results[-limit:]
    return results


# ---------------------------------------------------------------------------
# Per-agent "last seen" cursors
# ---------------------------------------------------------------------------


def _cursor_file(agent_id: str) -> Path:
    safe = "".join(c if c.isalnum() or c in ("-", "_", ".") else "_" for c in agent_id)
    return _cursors_dir() / f"{safe or 'unknown'}.json"


def set_cursor(agent_id: str, ts: Optional[str] = None, seq: Optional[int] = None) -> dict:
    """Record the 'last seen' point for an agent. Defaults to now."""
    data = {
        "agent_id": agent_id,
        "ts": ts or _iso_now(),
    }
    if seq is not None:
        data["seq"] = seq
    try:
        _cursors_dir().mkdir(parents=True, exist_ok=True)
        _cursor_file(agent_id).write_text(json.dumps(data, indent=2))
    except OSError as e:
        print(f"  [Stitch WARNING] Could not set cursor for {agent_id}: {e}", file=sys.stderr)
    return data


def get_cursor(agent_id: str) -> Optional[dict]:
    cf = _cursor_file(agent_id)
    if not cf.exists():
        return None
    try:
        return json.loads(cf.read_text())
    except (OSError, json.JSONDecodeError):
        return None


__all__ = [
    "EventFilter",
    "EVENT_TASK_CREATED",
    "EVENT_TASK_UPDATED",
    "EVENT_SNAPSHOT_ADDED",
    "EVENT_DECISION_ADDED",
    "EVENT_CONTEXT_UPDATED",
    "EVENT_HANDOFF_BUILT",
    "EVENT_TASK_MOVED",
    "ALL_EVENT_TYPES",
    "append_event",
    "read_events",
    "set_cursor",
    "get_cursor",
]
