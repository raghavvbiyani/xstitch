"""Storage engine for Stitch.

Machine-local storage:
  - Per-project data: `~/.stitch/projects/<project-key>/tasks/` (persists outside repo)
  - Global registry: `~/.stitch/registry.json` for cross-project task discovery

All data is JSON + Markdown — no database required.

TTL cleanup: tasks not updated in 45+ days are auto-removed to save disk space.
Runs opportunistically during Store init (max once per day).
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from dataclasses import asdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

from .models import Task, Snapshot, Decision, HandoffBundle, from_json, _now_iso

# Deduplication: skip snapshot if last one has >=80% word overlap within this window
_DEDUP_WINDOW_SECONDS = 120
_DEDUP_SIMILARITY_THRESHOLD = 0.8

# Quality: minimum meaningful message length
_MIN_SNAP_MESSAGE_LEN = 10
_MIN_DECISION_PROBLEM_LEN = 5

# TTL: tasks not updated in this many days get cleaned up
_TTL_DAYS = 45
# Cleanup runs at most once per day (tracked via ~/.stitch/.last_cleanup)
_CLEANUP_COOLDOWN_HOURS = 24

Stitch_DIR = ".stitch"
GLOBAL_HOME = Path.home() / ".stitch"
PROJECTS_HOME = GLOBAL_HOME / "projects"
REGISTRY_FILE = "registry.json"
ACTIVE_TASK_FILE = "active_task"


def project_key(project_path: Path) -> str:
    """Deterministic key for a project: <dirname>-<hash[:8]>."""
    path_hash = hashlib.md5(str(project_path).encode()).hexdigest()[:8]
    return f"{project_path.name}-{path_hash}"


class Store:
    """Manages task context storage at project and global level."""

    def __init__(self, project_path: Optional[str] = None):
        self.project_path = Path(project_path or os.getcwd()).resolve()
        self.project_key = project_key(self.project_path)
        self.local_dir = PROJECTS_HOME / self.project_key
        self.tasks_dir = self.local_dir / "tasks"
        try:
            GLOBAL_HOME.mkdir(parents=True, exist_ok=True)
            PROJECTS_HOME.mkdir(parents=True, exist_ok=True)
        except PermissionError:
            import sys
            print(
                f"  [Stitch WARNING] Cannot create {GLOBAL_HOME}\n"
                f"  [Stitch FIX] Grant write access: mkdir -p {GLOBAL_HOME} && chmod 755 {GLOBAL_HOME}\n"
                f"  [Stitch FIX] Or set Stitch_HOME env var to a writable directory",
                file=sys.stderr,
            )
            fallback = self.project_path / Stitch_DIR
            self.local_dir = fallback
            self.tasks_dir = fallback / "tasks"
            return
        self._migrate_from_repo()
        self._maybe_run_ttl_cleanup()

    # --- Migration ---

    def _migrate_from_repo(self):
        """Auto-migrate task data from old in-repo .stitch/ to ~/.stitch/projects/.

        Safety: copies data without deleting the original. The old .stitch/
        is left in place so the user can verify and clean up manually.
        """
        old_dir = self.project_path / Stitch_DIR
        old_tasks = old_dir / "tasks"
        if not old_tasks.exists() or not any(old_tasks.iterdir()):
            return
        if self.tasks_dir.exists() and any(self.tasks_dir.iterdir()):
            return

        try:
            from . import log
            log.info(f"Migrating task data from {old_dir} -> {self.local_dir}")

            self.local_dir.mkdir(parents=True, exist_ok=True)
            shutil.copytree(old_tasks, self.tasks_dir, dirs_exist_ok=True)

            for name in (ACTIVE_TASK_FILE, "AGENT_README.md", "TASK_INDEX.md", "task_index.json"):
                src = old_dir / name
                if src.exists():
                    shutil.copy2(src, self.local_dir / name)

            # Verify the copy succeeded before declaring success
            migrated_tasks = list(self.tasks_dir.iterdir()) if self.tasks_dir.exists() else []
            if migrated_tasks:
                log.ok(f"Migration complete. Data copied to {self.local_dir}")
                log.info(f"Old .stitch/ left at {old_dir} — safe to delete after verifying")
            else:
                log.warn("Migration may be incomplete — no tasks found at new location")
        except PermissionError as e:
            import sys
            print(f"  [Stitch WARNING] Permission denied during migration: {e}", file=sys.stderr)
            print(f"  [Stitch FIX] Ensure write access to {self.local_dir}", file=sys.stderr)
            print(f"  [Stitch FIX] Try: mkdir -p {self.local_dir} && chmod 755 {self.local_dir}", file=sys.stderr)
        except Exception as e:
            import sys
            print(f"  [Stitch WARNING] Migration failed: {e} — using old location as fallback", file=sys.stderr)
            self.local_dir = old_dir
            self.tasks_dir = old_dir / "tasks"

    # --- TTL Cleanup ---

    def _maybe_run_ttl_cleanup(self):
        """Run TTL cleanup if cooldown has elapsed (max once per day).

        Removes task data older than _TTL_DAYS from ~/.stitch/projects/.
        Only removes completed/abandoned tasks — active tasks are never touched.
        Runs silently; errors are swallowed to never block normal operations.
        """
        try:
            marker = GLOBAL_HOME / ".last_cleanup"
            now = datetime.now(timezone.utc)

            if marker.exists():
                last_run_ts = marker.read_text().strip()
                try:
                    last_run = datetime.fromisoformat(last_run_ts)
                    if (now - last_run).total_seconds() < _CLEANUP_COOLDOWN_HOURS * 3600:
                        return
                except (ValueError, TypeError):
                    pass

            removed = self._run_ttl_cleanup(now)

            marker.write_text(now.isoformat(timespec="seconds"))

            if removed:
                import sys
                print(
                    f"  [Stitch OK] TTL cleanup: removed {removed} task(s) "
                    f"older than {_TTL_DAYS} days",
                    file=sys.stderr,
                )
        except Exception:
            pass

    def _run_ttl_cleanup(self, now: datetime) -> int:
        """Walk all projects and remove stale tasks.

        A task is eligible for removal when ALL of these are true:
          1. updated_at is older than _TTL_DAYS
          2. status is NOT 'active' (active tasks are never cleaned up)
          3. It is NOT the current active task for its project

        Returns the count of removed tasks.
        """
        if not PROJECTS_HOME.exists():
            return 0

        cutoff = now - timedelta(days=_TTL_DAYS)
        removed = 0

        for project_dir in PROJECTS_HOME.iterdir():
            if not project_dir.is_dir():
                continue

            tasks_dir = project_dir / "tasks"
            if not tasks_dir.exists():
                continue

            active_task_file = project_dir / ACTIVE_TASK_FILE
            active_id = ""
            if active_task_file.exists():
                try:
                    active_id = active_task_file.read_text().strip()
                except OSError:
                    pass

            for task_dir in list(tasks_dir.iterdir()):
                if not task_dir.is_dir():
                    continue

                task_id = task_dir.name

                if task_id == active_id:
                    continue

                meta_file = task_dir / "meta.json"
                if not meta_file.exists():
                    continue

                try:
                    meta = json.loads(meta_file.read_text())
                except (json.JSONDecodeError, OSError):
                    continue

                if meta.get("status") == "active":
                    continue

                updated_at = meta.get("updated_at", "")
                try:
                    updated_dt = datetime.fromisoformat(updated_at)
                    if updated_dt >= cutoff:
                        continue
                except (ValueError, TypeError):
                    mtime = meta_file.stat().st_mtime
                    if datetime.fromtimestamp(mtime, tz=timezone.utc) >= cutoff:
                        continue

                try:
                    shutil.rmtree(task_dir)
                    removed += 1
                except OSError:
                    pass

        if removed:
            self._prune_registry_stale_entries()

        return removed

    def _prune_registry_stale_entries(self):
        """Remove entries from the global registry whose task dirs no longer exist."""
        try:
            registry = self._load_registry()
            tasks = registry.get("tasks", [])
            valid = [t for t in tasks if self._task_files_exist(from_json(Task, t))]
            if len(valid) < len(tasks):
                registry["tasks"] = valid
                self._save_registry(registry)
        except Exception:
            pass

    # --- Initialization ---

    def init_project(self) -> str:
        """Initialize Stitch in the current project."""
        try:
            self.local_dir.mkdir(parents=True, exist_ok=True)
            self.tasks_dir.mkdir(parents=True, exist_ok=True)
        except PermissionError:
            import sys
            print(
                f"  [Stitch ERROR] Cannot write to {self.local_dir}\n"
                f"  [Stitch FIX] Grant write access: chmod -R 755 {self.local_dir.parent}\n"
                f"  [Stitch FIX] Or ask your admin to allow writes to ~/.stitch/",
                file=sys.stderr,
            )
            raise

        agent_readme = self.local_dir / "AGENT_README.md"
        if not agent_readme.exists():
            agent_readme.write_text(self._agent_instructions())

        return str(self.local_dir)

    # --- Task CRUD ---

    def create_task(self, title: str, objective: str = "", tags: list[str] | None = None) -> Task:
        task = Task(
            title=title,
            project_path=str(self.project_path),
            objective=objective,
            tags=tags or [],
        )
        task_dir = self.tasks_dir / task.id
        task_dir.mkdir(parents=True, exist_ok=True)

        self._write_json(task_dir / "meta.json", asdict(task))
        self._write_json(task_dir / "snapshots.json", [])
        self._write_json(task_dir / "decisions.json", [])
        (task_dir / "context.md").write_text(
            f"# Task: {title}\n\n## Objective\n{objective}\n\n"
            f"## Current State\n\n## Next Steps\n"
        )

        self._set_active_task(task.id)
        self._register_task(task)
        return task

    def get_task(self, task_id: str) -> Optional[Task]:
        meta_file = self.tasks_dir / task_id / "meta.json"
        if not meta_file.exists():
            return None
        data = self._read_json(meta_file)
        return from_json(Task, data)

    def update_task(self, task: Task):
        task.touch()
        task_dir = self.tasks_dir / task.id
        self._write_json(task_dir / "meta.json", asdict(task))
        self._register_task(task)

    def list_tasks(self, project_only: bool = True) -> list[Task]:
        if project_only:
            tasks = []
            if self.tasks_dir.exists():
                for d in sorted(self.tasks_dir.iterdir()):
                    meta = d / "meta.json"
                    if meta.exists():
                        tasks.append(from_json(Task, self._read_json(meta)))
            return tasks

        registry = self._load_registry()
        tasks = [from_json(Task, t) for t in registry.get("tasks", [])]

        # Auto-prune stale entries (task files deleted / /tmp cleaned)
        valid = [t for t in tasks if self._task_files_exist(t)]
        if len(valid) < len(tasks):
            valid_ids = {t.id for t in valid}
            registry["tasks"] = [
                t for t in registry.get("tasks", [])
                if t.get("id") in valid_ids
            ]
            self._save_registry(registry)

        return valid

    @staticmethod
    def _task_files_exist(task: Task) -> bool:
        """Check whether the task's data files still exist on disk."""
        if not task.project_path:
            return False
        proj_path = Path(task.project_path)
        key = project_key(proj_path)
        new_meta = PROJECTS_HOME / key / "tasks" / task.id / "meta.json"
        if new_meta.exists():
            return True
        old_meta = proj_path / Stitch_DIR / "tasks" / task.id / "meta.json"
        return old_meta.exists()

    def task_is_local(self, task_id: str) -> bool:
        """Check if a task belongs to the current project (files exist locally)."""
        return (self.tasks_dir / task_id / "meta.json").exists()

    def get_task_project_path(self, task_id: str) -> str | None:
        """Look up the project path for a task from the global registry."""
        registry = self._load_registry()
        for t in registry.get("tasks", []):
            if t.get("id") == task_id:
                return t.get("project_path")
        return None

    def get_active_task_id(self) -> Optional[str]:
        f = self.local_dir / ACTIVE_TASK_FILE
        if f.exists():
            return f.read_text().strip()
        return None

    def _set_active_task(self, task_id: str):
        (self.local_dir / ACTIVE_TASK_FILE).write_text(task_id)

    def switch_task(self, task_id: str) -> bool:
        if (self.tasks_dir / task_id / "meta.json").exists():
            self._set_active_task(task_id)
            return True
        return False

    # --- Snapshots ---

    def add_snapshot(self, task_id: str, snapshot: Snapshot) -> str | None:
        """Add a snapshot with dedup and quality checks.

        Returns None on success, or a reason string if the snapshot was rejected.
        """
        snapshot.task_id = task_id

        msg = (snapshot.message or "").strip()
        if len(msg) < _MIN_SNAP_MESSAGE_LEN:
            return f"Snapshot rejected: message too short ({len(msg)} chars, need {_MIN_SNAP_MESSAGE_LEN}+). Be specific about what was done."

        snap_file = self.tasks_dir / task_id / "snapshots.json"
        snaps = self._read_json(snap_file) if snap_file.exists() else []

        # Dedup: skip if last snapshot is near-identical within time window
        if snaps:
            last = snaps[-1]
            if self._is_duplicate_snap(last, snapshot):
                return f"Snapshot skipped: too similar to previous snapshot from {last.get('timestamp', '?')}. Only push when there's new information."

        snaps.append(asdict(snapshot))

        # Keep sorted by timestamp (handles out-of-order daemon vs manual snaps)
        snaps.sort(key=lambda s: s.get("timestamp", ""))

        # Keep last 100 snapshots per task
        if len(snaps) > 100:
            snaps = snaps[-100:]
        self._write_json(snap_file, snaps)
        return None

    @staticmethod
    def _is_duplicate_snap(last_raw: dict, new: Snapshot) -> bool:
        """Check if new snapshot is near-duplicate of the last one."""
        last_ts = last_raw.get("timestamp", "")
        try:
            last_dt = datetime.fromisoformat(last_ts)
            new_dt = datetime.fromisoformat(new.timestamp)
            if abs((new_dt - last_dt).total_seconds()) > _DEDUP_WINDOW_SECONDS:
                return False
        except (ValueError, TypeError):
            return False

        last_msg = last_raw.get("message", "").lower()
        new_msg = (new.message or "").lower()
        if not last_msg or not new_msg:
            return False

        last_words = set(last_msg.split())
        new_words = set(new_msg.split())
        if not last_words or not new_words:
            return False

        overlap = len(last_words & new_words) / max(len(last_words | new_words), 1)
        return overlap >= _DEDUP_SIMILARITY_THRESHOLD

    def get_snapshots(self, task_id: str, limit: int = 10) -> list[Snapshot]:
        snap_file = self.tasks_dir / task_id / "snapshots.json"
        if not snap_file.exists():
            return []
        data = self._read_json(snap_file)
        data.sort(key=lambda s: s.get("timestamp", ""))
        return [from_json(Snapshot, s) for s in data[-limit:]]

    # --- Decisions ---

    def add_decision(self, task_id: str, decision: Decision) -> str | None:
        """Add a decision with dedup and quality checks.

        Returns None on success, or a reason string if the decision was rejected.
        """
        decision.task_id = task_id

        problem = (decision.problem or "").strip()
        chosen = (decision.chosen or "").strip()
        if len(problem) < _MIN_DECISION_PROBLEM_LEN:
            return f"Decision rejected: 'problem' too short. Describe what problem was being solved."
        if not chosen:
            return "Decision rejected: 'chosen' is empty. Describe what solution was chosen."

        dec_file = self.tasks_dir / task_id / "decisions.json"
        decs = self._read_json(dec_file) if dec_file.exists() else []

        # Dedup: skip if same problem already recorded
        problem_lower = problem.lower()
        for existing in decs:
            if existing.get("problem", "").lower() == problem_lower:
                return f"Decision skipped: a decision for '{problem[:40]}' already exists. Use a different problem description if this is a new decision."

        decs.append(asdict(decision))
        # Keep sorted by timestamp
        decs.sort(key=lambda d: d.get("timestamp", ""))
        self._write_json(dec_file, decs)
        return None

    def get_decisions(self, task_id: str) -> list[Decision]:
        dec_file = self.tasks_dir / task_id / "decisions.json"
        if not dec_file.exists():
            return []
        data = self._read_json(dec_file)
        data.sort(key=lambda d: d.get("timestamp", ""))
        return [from_json(Decision, d) for d in data]

    # --- Handoff Bundle ---

    def build_handoff(self, task_id: str, token_budget: int = 3000) -> Optional[HandoffBundle]:
        task = self.get_task(task_id)
        if not task:
            return None
        snapshots = self.get_snapshots(task_id, limit=5)
        decisions = self.get_decisions(task_id)
        bundle = HandoffBundle(
            task=task,
            recent_snapshots=snapshots,
            key_decisions=decisions[-5:],
            token_budget=token_budget,
        )
        # Write generated handoff to disk
        handoff_file = self.tasks_dir / task_id / "handoff.md"
        handoff_file.write_text(bundle.to_markdown())
        return bundle

    # --- Search ---

    def search_tasks(self, query: str) -> list[Task]:
        """Search tasks by keyword across title, objective, tags, and decisions."""
        query_lower = query.lower()
        results = []
        for task in self.list_tasks(project_only=False):
            score = 0
            if query_lower in task.title.lower():
                score += 3
            if query_lower in task.objective.lower():
                score += 2
            if any(query_lower in t.lower() for t in task.tags):
                score += 2
            if query_lower in task.current_state.lower():
                score += 1
            if score > 0:
                results.append((score, task))

        # Also search decisions
        for task in self.list_tasks(project_only=True):
            decisions = self.get_decisions(task.id)
            for d in decisions:
                if query_lower in d.problem.lower() or query_lower in d.chosen.lower():
                    results.append((1, task))
                    break

        seen = set()
        unique = []
        for score, task in sorted(results, key=lambda x: -x[0]):
            if task.id not in seen:
                seen.add(task.id)
                unique.append(task)
        return unique

    # --- Global Registry (PageIndex-like) ---

    def _load_registry(self) -> dict:
        reg_file = GLOBAL_HOME / REGISTRY_FILE
        if reg_file.exists():
            try:
                return json.loads(reg_file.read_text())
            except (json.JSONDecodeError, OSError):
                return {"tasks": []}
        return {"tasks": []}

    def _save_registry(self, registry: dict):
        reg_file = GLOBAL_HOME / REGISTRY_FILE
        reg_file.write_text(json.dumps(registry, indent=2, default=str))

    def _register_task(self, task: Task):
        registry = self._load_registry()
        tasks = registry.get("tasks", [])

        # Update or append
        updated = False
        for i, t in enumerate(tasks):
            if t.get("id") == task.id:
                tasks[i] = asdict(task)
                updated = True
                break
        if not updated:
            tasks.append(asdict(task))

        registry["tasks"] = tasks
        self._save_registry(registry)

    # --- Context file (human-readable living doc) ---

    def update_context_file(self, task_id: str):
        """Regenerate the human-readable context.md for a task."""
        task = self.get_task(task_id)
        if not task:
            return
        snapshots = self.get_snapshots(task_id, limit=10)
        decisions = self.get_decisions(task_id)

        lines = [
            f"# Task: {task.title}",
            f"**ID**: `{task.id}` | **Status**: {task.status} | **Updated**: {task.updated_at}",
            "",
            "## Objective",
            task.objective or "(not set)",
            "",
            "## Current State",
            task.current_state or "(not set)",
            "",
            "## Next Steps",
            task.next_steps or "(not set)",
            "",
        ]

        if task.blockers:
            lines += ["## Blockers", task.blockers, ""]

        if decisions:
            lines += ["## Decisions"]
            for d in decisions:
                lines.append(d.to_markdown())
            lines.append("")

        if snapshots:
            lines += ["## Recent Snapshots"]
            for s in snapshots:
                lines.append(s.to_markdown())
            lines.append("")

        (self.tasks_dir / task_id / "context.md").write_text("\n".join(lines))

    # --- Agent instructions ---

    def _agent_instructions(self) -> str:
        cli = "python3 -m xstitch.cli"
        return (
            "# Stitch — Agent Instructions\n\n"
            "This directory contains task context managed by the Agent Handoff & Context Protocol.\n"
            "Stitch prevents duplicate notes automatically — push freely.\n\n"
            "## For AI Agents\n"
            f"1. Run `{cli} auto-setup` then `{cli} auto \"<user message>\"` at session start.\n"
            "2. Read `.stitch/tasks/<task-id>/context.md` for full context.\n"
            "3. Check `decisions.json` before making decisions — avoid repeating failed experiments.\n\n"
            "### WHEN to Push\n"
            f"- After completing a sub-task: `{cli} snap -m \"what was done + outcome\"`\n"
            f"- After a design decision: `{cli} decide -p \"problem\" -c \"chosen\" -a \"alts\" -r \"why\"`\n"
            f"- After a failed experiment: `{cli} snap -m \"FAILED: what + why\"`\n"
            f"- Every 3-5 meaningful actions: `{cli} snap -m \"progress summary\"`\n"
            f"- Before ending session: `{cli} checkpoint -s \"summary\" -d \"decisions\" -e \"experiments\" -f \"failures\" -q \"questions\"`\n\n"
            "## For Humans\n"
            f"- `{cli} task list` — see all tasks\n"
            f"- `{cli} handoff` — get a copy-pasteable context bundle for a new AI tool\n"
            f"- `{cli} search <query>` — find tasks by keyword\n"
        )

    # --- Helpers ---

    @staticmethod
    def _write_json(path: Path, data):
        content = json.dumps(data, indent=2, default=str)
        tmp = path.with_suffix(".tmp")
        try:
            tmp.write_text(content)
            tmp.replace(path)
        except OSError:
            path.write_text(content)

    @staticmethod
    def _read_json(path: Path):
        try:
            return json.loads(path.read_text())
        except json.JSONDecodeError:
            import sys
            print(f"  [Stitch WARNING] Corrupted JSON: {path} — returning empty", file=sys.stderr)
            return {} if "meta" in path.name else []
        except OSError as e:
            import sys
            print(f"  [Stitch WARNING] Cannot read {path}: {e}", file=sys.stderr)
            return {} if "meta" in path.name else []
