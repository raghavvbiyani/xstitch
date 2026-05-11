"""Context Sync Engine for Stitch — freshness detection, conflict resolution.

CRDT-inspired approach to reconciling saved context vs current reality:
  - LWW-Register (Last-Writer-Wins) for scalar task fields: newest timestamp wins
  - G-Set (Grow-Only) for decisions/snapshots: append-only, never lose information
  - User Arbitration for semantic conflicts that timestamps cannot resolve

Zero external dependencies — stdlib only.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

from .capture import run_git, is_git_repo
from .models import Task, Snapshot


# ── Freshness thresholds ─────────────────────────────────────────────────────

_FRESH_SECONDS = 3600          # < 1 hour
_RECENT_SECONDS = 86400        # < 24 hours
_STALE_SECONDS = 604800        # < 7 days

_SESSION_STATE_FILE = Path.home() / ".ahcp" / "session_state.json"


# ── Data models ──────────────────────────────────────────────────────────────

@dataclass
class Conflict:
    """A mismatch between saved Stitch context and current reality."""
    conflict_type: str      # BRANCH_DIVERGED, FILES_MODIFIED_SINCE, etc.
    severity: str           # auto_resolved | needs_user_input | informational
    saved_value: str
    current_value: str
    resolution: str | None = None
    question_for_user: str | None = None
    conflict_id: str = ""   # short identifier for CLI resolution


@dataclass
class ContextVerification:
    """Result of verifying saved context against current reality."""
    freshness_category: str     # FRESH | RECENT | STALE | OUTDATED
    age_seconds: float
    age_human: str
    sessions_since: int
    conflicts: list[Conflict] = field(default_factory=list)
    auto_resolved: list[Conflict] = field(default_factory=list)
    needs_user_input: list[Conflict] = field(default_factory=list)
    reality: dict = field(default_factory=dict)


# ── Core engine ──────────────────────────────────────────────────────────────

class ContextSyncEngine:
    """Compares saved Stitch context against current filesystem/git reality."""

    @staticmethod
    def verify(task: Task, store, snapshots: list[Snapshot] | None = None) -> ContextVerification:
        """Run all checks and return a complete verification report.

        This is the main entry point. It:
        1. Computes freshness category from task.updated_at
        2. Checks git state divergence (branch, uncommitted changes)
        3. Checks file modification times against snapshot timestamps
        4. Checks for concurrent session activity
        5. Checks next_steps relevance against recent git commits
        6. Auto-resolves what it can, flags the rest for user input
        """
        if snapshots is None:
            snapshots = store.get_snapshots(task.id, limit=20)

        category, age_secs, age_human = ContextSyncEngine._compute_freshness(task)
        sessions_since = ContextSyncEngine._count_sessions_since(task)

        all_conflicts: list[Conflict] = []
        project_path = task.project_path

        reality: dict = {}
        if project_path and os.path.isdir(project_path) and is_git_repo(project_path):
            reality = ContextSyncEngine._capture_reality(project_path)
            all_conflicts.extend(
                ContextSyncEngine._check_git_state(task, snapshots, reality)
            )
            all_conflicts.extend(
                ContextSyncEngine._check_file_freshness(snapshots, project_path)
            )
            all_conflicts.extend(
                ContextSyncEngine._check_next_steps_relevance(task, project_path)
            )

        all_conflicts.extend(
            ContextSyncEngine._check_concurrent_sessions(task)
        )

        if category in ("STALE", "OUTDATED"):
            all_conflicts.append(Conflict(
                conflict_type="STATE_STALE",
                severity="informational",
                saved_value=f"Last updated {age_human}",
                current_value=f"Category: {category}",
                resolution=f"Context is {category.lower()}. Verify before relying on it.",
                conflict_id="stale-0",
            ))

        auto_resolved = [c for c in all_conflicts if c.severity == "auto_resolved"]
        needs_input = [c for c in all_conflicts if c.severity == "needs_user_input"]

        return ContextVerification(
            freshness_category=category,
            age_seconds=age_secs,
            age_human=age_human,
            sessions_since=sessions_since,
            conflicts=all_conflicts,
            auto_resolved=auto_resolved,
            needs_user_input=needs_input,
            reality=reality,
        )

    # ── Freshness ────────────────────────────────────────────────────────

    @staticmethod
    def _compute_freshness(task: Task) -> tuple[str, float, str]:
        """Categorize task freshness from updated_at timestamp.

        Returns (category, age_seconds, human_readable_age).
        """
        try:
            updated = datetime.fromisoformat(task.updated_at)
            if updated.tzinfo is None:
                updated = updated.replace(tzinfo=timezone.utc)
            age = (datetime.now(timezone.utc) - updated).total_seconds()
        except (ValueError, TypeError):
            return ("OUTDATED", float("inf"), "unknown")

        age = max(0.0, age)

        if age < _FRESH_SECONDS:
            human = _humanize_seconds(age)
            return ("FRESH", age, human)
        elif age < _RECENT_SECONDS:
            human = _humanize_seconds(age)
            return ("RECENT", age, human)
        elif age < _STALE_SECONDS:
            human = _humanize_seconds(age)
            return ("STALE", age, human)
        else:
            human = _humanize_seconds(age)
            return ("OUTDATED", age, human)

    # ── Git state checks ─────────────────────────────────────────────────

    @staticmethod
    def _capture_reality(project_path: str) -> dict:
        """Snapshot the current git/filesystem state."""
        branch = run_git(["branch", "--show-current"], cwd=project_path)
        status = run_git(["status", "--short"], cwd=project_path)
        last_commit = run_git(
            ["log", "-1", "--format=%H %s (%ar)"], cwd=project_path
        )
        uncommitted = len(status.strip().splitlines()) if status.strip() else 0
        return {
            "branch": branch,
            "status_short": status,
            "last_commit": last_commit,
            "uncommitted_count": uncommitted,
            "clean": uncommitted == 0,
        }

    @staticmethod
    def _check_git_state(
        task: Task, snapshots: list[Snapshot], reality: dict
    ) -> list[Conflict]:
        """Compare saved branch/status against current git state."""
        conflicts: list[Conflict] = []

        saved_branch = ""
        for s in reversed(snapshots):
            if s.git_branch:
                saved_branch = s.git_branch
                break

        current_branch = reality.get("branch", "")
        if saved_branch and current_branch and saved_branch != current_branch:
            conflicts.append(Conflict(
                conflict_type="BRANCH_DIVERGED",
                severity="auto_resolved",
                saved_value=saved_branch,
                current_value=current_branch,
                resolution=f"Branch changed: `{saved_branch}` -> `{current_branch}` (current git wins)",
                conflict_id="branch-0",
            ))

        saved_clean = True
        for s in reversed(snapshots):
            if s.git_status:
                saved_clean = s.git_status.strip() == ""
                break

        current_clean = reality.get("clean", True)
        if saved_clean != current_clean:
            if current_clean:
                desc = "Was dirty, now clean"
            else:
                count = reality.get("uncommitted_count", 0)
                desc = f"Was clean, now {count} uncommitted file(s)"
            conflicts.append(Conflict(
                conflict_type="UNCOMMITTED_DRIFT",
                severity="auto_resolved",
                saved_value="clean" if saved_clean else "dirty",
                current_value="clean" if current_clean else f"{reality.get('uncommitted_count', 0)} uncommitted",
                resolution=f"{desc} (current git wins)",
                conflict_id="drift-0",
            ))

        return conflicts

    # ── File freshness ───────────────────────────────────────────────────

    @staticmethod
    def _check_file_freshness(
        snapshots: list[Snapshot], project_path: str
    ) -> list[Conflict]:
        """Check if files mentioned in snapshots were modified after the snapshot."""
        if not snapshots:
            return []

        last_snap = snapshots[-1]
        try:
            snap_time = datetime.fromisoformat(last_snap.timestamp)
            if snap_time.tzinfo is None:
                snap_time = snap_time.replace(tzinfo=timezone.utc)
            snap_epoch = snap_time.timestamp()
        except (ValueError, TypeError):
            return []

        modified_files: list[str] = []
        all_files: set[str] = set()
        for s in snapshots:
            all_files.update(s.files_changed[:15])

        for fpath in sorted(all_files)[:20]:
            full = os.path.join(project_path, fpath) if not os.path.isabs(fpath) else fpath
            try:
                mtime = os.path.getmtime(full)
                if mtime > snap_epoch + 5:
                    modified_files.append(fpath)
            except OSError:
                pass

        if modified_files:
            return [Conflict(
                conflict_type="FILES_MODIFIED_SINCE",
                severity="auto_resolved",
                saved_value=f"{len(all_files)} tracked file(s) at last snapshot",
                current_value=f"{len(modified_files)} file(s) modified since last snapshot",
                resolution=(
                    f"{len(modified_files)} file(s) changed since last snapshot "
                    f"(reality wins): {', '.join(modified_files[:5])}"
                    + (" ..." if len(modified_files) > 5 else "")
                ),
                conflict_id="files-0",
            )]
        return []

    # ── Concurrent session detection ─────────────────────────────────────

    @staticmethod
    def _check_concurrent_sessions(task: Task) -> list[Conflict]:
        """Detect if another session modified this task since we last interacted."""
        try:
            if not _SESSION_STATE_FILE.exists():
                return []
            state = json.loads(_SESSION_STATE_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            return []

        stop_time_str = state.get("stop_time", "")
        stop_session_id = state.get("stop_session_id", "")

        if not stop_time_str or not stop_session_id:
            return []

        try:
            task_updated = datetime.fromisoformat(task.updated_at)
            if task_updated.tzinfo is None:
                task_updated = task_updated.replace(tzinfo=timezone.utc)

            stop_time = datetime.fromisoformat(stop_time_str)
            if stop_time.tzinfo is None:
                stop_time = stop_time.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            return []

        if stop_time > task_updated + timedelta(seconds=10):
            recent_tools = state.get("recent_tools", [])
            tool_summary = "; ".join(recent_tools[-3:]) if recent_tools else "(no details)"
            return [Conflict(
                conflict_type="CONCURRENT_SESSION",
                severity="needs_user_input",
                saved_value=f"Task last updated: {task.updated_at}",
                current_value=f"Another session ended at {stop_time_str} — actions: {tool_summary}",
                question_for_user=(
                    "Another session modified this task after the saved context was last updated. "
                    f"Recent actions: {tool_summary}. "
                    "Is the saved context still accurate, or has something changed?"
                ),
                conflict_id="concurrent-0",
            )]

        return []

    # ── Next-steps relevance ─────────────────────────────────────────────

    @staticmethod
    def _check_next_steps_relevance(task: Task, project_path: str) -> list[Conflict]:
        """Check if next_steps mentions work that git log suggests is already done."""
        if not task.next_steps or not task.next_steps.strip():
            return []

        file_pattern = re.findall(r'[\w./-]+\.\w{1,6}', task.next_steps)
        keywords = re.findall(r'\b(?:fix|add|update|refactor|implement|create|write|test)\b',
                              task.next_steps.lower())

        if not file_pattern and not keywords:
            return []

        recent_log = run_git(
            ["log", "--oneline", "-10", "--format=%s"],
            cwd=project_path,
        )
        if not recent_log:
            return []

        log_lower = recent_log.lower()
        matched_keywords = [k for k in keywords if k in log_lower]
        matched_files = [f for f in file_pattern if f.lower() in log_lower]

        if matched_keywords or matched_files:
            evidence = matched_keywords + matched_files
            return [Conflict(
                conflict_type="NEXT_STEPS_OUTDATED",
                severity="auto_resolved",
                saved_value=f"Next steps mention: {', '.join(evidence[:5])}",
                current_value=f"Recent commits appear to address: {', '.join(evidence[:5])}",
                resolution=(
                    "Some next steps may already be completed based on recent git commits. "
                    "Verify before repeating work."
                ),
                conflict_id="nextsteps-0",
            )]

        return []

    # ── Session counting ─────────────────────────────────────────────────

    @staticmethod
    def _count_sessions_since(task: Task) -> int:
        """Estimate sessions since last task update using session_count field."""
        return getattr(task, "session_count", 0)

    # ── Formatting ───────────────────────────────────────────────────────

    @staticmethod
    def format_freshness_report(v: ContextVerification) -> str:
        """Render a markdown freshness report for injection into the briefing."""
        lines = [
            "## Context Freshness Report",
            f"**Last updated**: {v.age_human} ({v.freshness_category})",
        ]
        if v.sessions_since > 0:
            lines.append(f"**Sessions since last update**: {v.sessions_since}")

        if v.reality:
            branch = v.reality.get("branch", "?")
            uncommitted = v.reality.get("uncommitted_count", 0)
            clean_str = "clean" if uncommitted == 0 else f"{uncommitted} uncommitted file(s)"
            lines.append(f"**Reality check**: branch `{branch}`, {clean_str}")

        lines.append("")

        if v.auto_resolved:
            lines.append("### Auto-Resolved (no action needed)")
            for c in v.auto_resolved:
                lines.append(f"- {c.resolution}")
            lines.append("")

        if v.needs_user_input:
            lines.append("### Requires Your Input (ASK THE USER)")
            for i, c in enumerate(v.needs_user_input, 1):
                lines.append(f"- [{c.conflict_id}] {c.conflict_type}")
                lines.append(f"  Saved: {c.saved_value}")
                lines.append(f"  Current: {c.current_value}")
                if c.question_for_user:
                    lines.append(f"  [ASK USER]: \"{c.question_for_user}\"")
            lines.append("")

        informational = [c for c in v.conflicts if c.severity == "informational"]
        if informational:
            lines.append("### Informational")
            for c in informational:
                lines.append(f"- {c.conflict_type}: {c.resolution or c.current_value}")
            lines.append("")

        if not v.conflicts:
            lines.append("No conflicts detected. Saved context appears consistent with current reality.")
            lines.append("")

        return "\n".join(lines)

    @staticmethod
    def format_sync_protocol(v: ContextVerification) -> str:
        """Render the mandatory context sync protocol header for agents."""
        conflict_summary = (
            f"{len(v.auto_resolved)} auto-resolved, "
            f"{len(v.needs_user_input)} needs user input"
        )

        lines = [
            "Stitch CONTEXT SYNC PROTOCOL (MANDATORY):",
            f"Freshness: {v.freshness_category} ({v.age_human}) | "
            f"Conflicts: {conflict_summary}",
            "",
            "1. READ the full Context Freshness Report below",
        ]

        if v.needs_user_input:
            lines.append(
                "2. STOP — there are unresolved conflicts. "
                "Ask the user to resolve them BEFORE doing any work"
            )
            lines.append(
                "3. After user resolves, record: "
                "stitch context-resolve --conflict-id <id> --resolution \"<answer>\""
            )
            lines.append(
                "4. CONFIRM your understanding: "
                "\"I've reviewed the Stitch context. Here's my understanding: [summary]\""
            )
            lines.append("5. Only THEN proceed with the task")
        else:
            lines.append(
                "2. CONFIRM your understanding: "
                "\"I've reviewed the Stitch context. Here's my understanding: [summary]\""
            )
            lines.append("3. Then proceed with the task")

        lines.append("")
        return "\n".join(lines)


# ── Utility ──────────────────────────────────────────────────────────────────

def _humanize_seconds(seconds: float) -> str:
    """Convert seconds to a human-readable string like '2 hours ago'."""
    if seconds < 60:
        return "just now"
    elif seconds < 3600:
        mins = int(seconds / 60)
        return f"{mins} minute{'s' if mins != 1 else ''} ago"
    elif seconds < 86400:
        hours = int(seconds / 3600)
        return f"{hours} hour{'s' if hours != 1 else ''} ago"
    else:
        days = int(seconds / 86400)
        return f"{days} day{'s' if days != 1 else ''} ago"
