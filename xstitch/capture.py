"""Auto-capture module for Stitch.

Captures git state, file changes, and other environment info
to create rich snapshots automatically. Token-aware: truncates
large outputs to keep snapshots compact.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Optional

from .models import Snapshot, _now_iso

MAX_DIFF_CHARS = 1500
MAX_STATUS_CHARS = 800
MAX_LOG_CHARS = 600


def run_git(args: list[str], cwd: str | None = None) -> str:
    """Run a git command and return stdout, or empty string on failure."""
    try:
        result = subprocess.run(
            ["git"] + args,
            capture_output=True,
            text=True,
            timeout=10,
            cwd=cwd or os.getcwd(),
        )
        return result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return ""


def is_git_repo(path: str | None = None) -> bool:
    return bool(run_git(["rev-parse", "--is-inside-work-tree"], cwd=path))


def capture_git_state(cwd: str | None = None) -> dict:
    """Capture current git state as a dict of strings."""
    if not is_git_repo(cwd):
        return {}

    return {
        "branch": run_git(["branch", "--show-current"], cwd=cwd),
        "status": _truncate(run_git(["status", "--short"], cwd=cwd), MAX_STATUS_CHARS),
        "diff_stat": _truncate(run_git(["diff", "--stat"], cwd=cwd), MAX_DIFF_CHARS),
        "diff_staged_stat": _truncate(
            run_git(["diff", "--cached", "--stat"], cwd=cwd), MAX_DIFF_CHARS
        ),
        "log_short": _truncate(
            run_git(["log", "--oneline", "-10"], cwd=cwd), MAX_LOG_CHARS
        ),
        "last_commit": run_git(
            ["log", "-1", "--format=%h %s (%ar)"], cwd=cwd
        ),
    }


def capture_snapshot(
    message: str = "",
    source: str = "manual",
    cwd: str | None = None,
    task_id: str = "",
) -> Snapshot:
    """Create a rich snapshot of current project state."""
    git = capture_git_state(cwd)

    # Detect changed files from git status
    status_lines = git.get("status", "").split("\n")
    files_changed = []
    for line in status_lines:
        line = line.strip()
        if line and len(line) > 3:
            files_changed.append(line[3:].strip())

    return Snapshot(
        task_id=task_id,
        timestamp=_now_iso(),
        message=message or _auto_message(git),
        source=source,
        git_branch=git.get("branch", ""),
        git_diff_stat=git.get("diff_stat", ""),
        git_status=git.get("status", ""),
        git_log_short=git.get("log_short", ""),
        files_changed=files_changed[:20],
    )


def has_significant_changes(cwd: str | None = None) -> bool:
    """Check if there are meaningful changes worth snapshotting."""
    if not is_git_repo(cwd):
        return False

    status = run_git(["status", "--short"], cwd=cwd)
    if not status.strip():
        return False

    lines = [l for l in status.strip().split("\n") if l.strip()]
    # At least 1 file changed
    return len(lines) >= 1


def capture_pre_summarize_snapshot(
    summary: str,
    decisions_made: str = "",
    experiments: str = "",
    failures: str = "",
    open_questions: str = "",
    cwd: str | None = None,
    task_id: str = "",
) -> Snapshot:
    """Richer snapshot for when an agent's context is about to be summarized.

    This captures not just git state but the agent's reasoning, decisions,
    and open threads that would otherwise be lost during chat summarization.
    """
    git = capture_git_state(cwd)

    extra_parts = []
    if decisions_made:
        extra_parts.append(f"DECISIONS: {decisions_made}")
    if experiments:
        extra_parts.append(f"EXPERIMENTS: {experiments}")
    if failures:
        extra_parts.append(f"FAILURES/DEAD-ENDS: {failures}")
    if open_questions:
        extra_parts.append(f"OPEN QUESTIONS: {open_questions}")

    extra_context = "\n".join(extra_parts)

    status_lines = git.get("status", "").split("\n")
    files_changed = [l.strip()[3:].strip() for l in status_lines if l.strip() and len(l.strip()) > 3]

    message = f"PRE-SUMMARIZE CHECKPOINT: {summary}"
    if extra_context:
        message += f"\n---\n{extra_context}"

    return Snapshot(
        task_id=task_id,
        timestamp=_now_iso(),
        message=message,
        source="pre-summarize",
        git_branch=git.get("branch", ""),
        git_diff_stat=git.get("diff_stat", ""),
        git_status=git.get("status", ""),
        git_log_short=git.get("log_short", ""),
        files_changed=files_changed[:20],
        extra={
            "decisions_made": decisions_made,
            "experiments": experiments,
            "failures": failures,
            "open_questions": open_questions,
        },
    )


def _auto_message(git: dict) -> str:
    """Generate an automatic snapshot message from git state."""
    branch = git.get("branch", "unknown")
    last = git.get("last_commit", "")
    status = git.get("status", "")
    n_changes = len([l for l in status.split("\n") if l.strip()])

    parts = [f"Auto-snapshot on `{branch}`"]
    if n_changes:
        parts.append(f"({n_changes} files changed)")
    if last:
        parts.append(f"— last commit: {last}")
    return " ".join(parts)


def _truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n... (truncated)"
