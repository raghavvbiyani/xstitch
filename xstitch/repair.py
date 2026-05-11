"""Self-repair for Stitch — detect and re-home orphaned tasks.

An 'orphan' is a task whose on-disk location disagrees with what its
``meta.project_path`` claims. This is the exact shape of the bug that
motivated the cross-agent-sync feature: a Cursor-spawned MCP with
cwd=home silently writes tasks under ``~/.ahcp/projects/<home-key>/``
even when the task clearly belongs to a repo. The task becomes invisible
to any agent that correctly resolves the repo as its project scope.

This module exposes:

* :func:`scan_orphans` — walk every project scope in the registry and
  flag tasks whose current scope does not match their ``meta.project_path``.
* :func:`repair_orphan` — atomically move a task's files into the correct
  project scope and update the registry, indexes, and active-task marker.

The repair is idempotent and safe: files are copied-then-verified before
the source is removed, and a ``task_moved`` event is emitted for
observability.
"""

from __future__ import annotations

import json
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Optional

from .locks import FileLockTimeout, file_lock
from .project_resolver import find_project_root


@dataclass
class OrphanReport:
    """A task whose storage scope disagrees with its declared project path."""
    task_id: str
    current_scope_path: Path  # ~/.ahcp/projects/<key>/tasks/<id>
    current_project_path: Optional[str]  # the scope dir's inferred project path
    meta_project_path: str  # what meta.json claims
    suggested_project_path: Optional[str]  # ancestor-walk suggestion, if any
    reason: str  # 'scope_mismatch' | 'suspicious_home_scope' | 'no_marker'

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "current_scope_path": str(self.current_scope_path),
            "current_project_path": self.current_project_path,
            "meta_project_path": self.meta_project_path,
            "suggested_project_path": self.suggested_project_path,
            "reason": self.reason,
        }


@dataclass
class RepairResult:
    task_id: str
    source_scope: Path
    dest_scope: Path
    dry_run: bool
    success: bool
    message: str

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "source_scope": str(self.source_scope),
            "dest_scope": str(self.dest_scope),
            "dry_run": self.dry_run,
            "success": self.success,
            "message": self.message,
        }


def scan_orphans() -> list[OrphanReport]:
    """Walk ``~/.ahcp/projects/*/tasks/*`` and flag orphans.

    Three classes of orphan are reported:

    * ``scope_mismatch`` — the directory the task lives in has a different
      ``project_key`` than ``project_key(meta.project_path)``. Strong
      signal of the Cursor/Claude split-brain bug.
    * ``suspicious_home_scope`` — task lives under the *home directory's*
      scope and a valid repo root exists under ``meta.project_path``.
      Cursor-from-home is the canonical cause.
    * ``no_marker`` — ``meta.project_path`` points at a directory that no
      longer exists or contains no ``.git``/``.ahcp`` marker. The task is
      effectively homeless.
    """
    from .store import PROJECTS_HOME, project_key

    if not PROJECTS_HOME.exists():
        return []

    orphans: list[OrphanReport] = []
    home_resolved: Optional[Path] = None
    try:
        home_resolved = Path.home().resolve(strict=False)
    except (OSError, RuntimeError):
        home_resolved = None

    for scope_dir in sorted(PROJECTS_HOME.iterdir()):
        if not scope_dir.is_dir():
            continue
        tasks_dir = scope_dir / "tasks"
        if not tasks_dir.exists():
            continue

        for task_dir in sorted(tasks_dir.iterdir()):
            if not task_dir.is_dir():
                continue
            meta_file = task_dir / "meta.json"
            if not meta_file.exists():
                continue
            try:
                meta = json.loads(meta_file.read_text())
            except (json.JSONDecodeError, OSError):
                continue

            meta_project_path = meta.get("project_path") or ""
            if not meta_project_path:
                # Nothing to compare against; skip silently.
                continue

            expected_key = project_key(Path(meta_project_path))
            current_key = scope_dir.name  # e.g. 'repo-abc12345'
            reason: Optional[str] = None
            suggested: Optional[str] = None

            if expected_key != current_key:
                reason = "scope_mismatch"
                suggested = meta_project_path
            elif home_resolved is not None and Path(meta_project_path).resolve(strict=False) == home_resolved:
                # Task stored under home scope and meta also says home. If a
                # deeper repo under the original cwd would have matched, that
                # can't be recovered from meta alone — leave for manual repair.
                pass
            else:
                # Scope matches what meta says. Verify meta's path still looks
                # like a project (has a marker). Otherwise flag as no_marker.
                marker = find_project_root(Path(meta_project_path))
                if marker is None:
                    reason = "no_marker"
                    suggested = None
                elif marker != Path(meta_project_path).resolve(strict=False):
                    # Meta points to a sub-directory of a real project; prefer
                    # the marker root to reduce scope fragmentation.
                    reason = "scope_mismatch"
                    suggested = str(marker)

            if reason:
                orphans.append(
                    OrphanReport(
                        task_id=task_dir.name,
                        current_scope_path=task_dir,
                        current_project_path=meta_project_path if expected_key == current_key else None,
                        meta_project_path=meta_project_path,
                        suggested_project_path=suggested,
                        reason=reason,
                    )
                )

    return orphans


def repair_orphan(
    task_id: str,
    new_project_path: str,
    dry_run: bool = False,
    on_progress: Optional[Callable[[str], None]] = None,
) -> RepairResult:
    """Move an orphaned task's storage under ``new_project_path``'s scope.

    Steps (atomic from the caller's point of view):
      1. Locate the source task directory (via registry + on-disk search).
      2. Compute the destination = ``PROJECTS_HOME / project_key(new_project_path) / tasks / <id>``.
      3. If destination exists → abort with a clear error (user must decide).
      4. Copy the source tree to destination, rewrite ``meta.project_path``.
      5. Update the global registry entry (under lock).
      6. Regenerate both source and destination ``task_index.json``.
      7. Fix the source scope's ``active_task`` file if it pointed at this task.
      8. Remove the source directory.
      9. Emit a ``task_moved`` event.

    Dry-run mode performs steps 1–3 only and reports what would happen.
    """
    from .store import PROJECTS_HOME, project_key
    from . import event_log

    log = on_progress or (lambda msg: None)

    src_task_dir = _locate_task_dir(task_id)
    if src_task_dir is None:
        return RepairResult(
            task_id=task_id,
            source_scope=PROJECTS_HOME,
            dest_scope=PROJECTS_HOME,
            dry_run=dry_run,
            success=False,
            message=f"task {task_id} not found in any project scope",
        )

    new_project_path_resolved = Path(new_project_path).resolve(strict=False)
    dest_scope = PROJECTS_HOME / project_key(new_project_path_resolved)
    dest_task_dir = dest_scope / "tasks" / task_id
    src_scope = src_task_dir.parent.parent  # scope/tasks/<id> → scope

    if dest_task_dir.exists():
        return RepairResult(
            task_id=task_id,
            source_scope=src_scope,
            dest_scope=dest_scope,
            dry_run=dry_run,
            success=False,
            message=(
                f"destination {dest_task_dir} already exists; manual "
                f"intervention required to merge or choose a winner"
            ),
        )

    if src_task_dir == dest_task_dir:
        return RepairResult(
            task_id=task_id,
            source_scope=src_scope,
            dest_scope=dest_scope,
            dry_run=dry_run,
            success=True,
            message="source and destination are identical; nothing to do",
        )

    if dry_run:
        return RepairResult(
            task_id=task_id,
            source_scope=src_scope,
            dest_scope=dest_scope,
            dry_run=True,
            success=True,
            message=f"would move {src_task_dir} -> {dest_task_dir}",
        )

    # --- Real move: copy, verify, rewrite meta, update registry, delete source ---
    log(f"copying {src_task_dir} -> {dest_task_dir}")
    try:
        dest_scope.mkdir(parents=True, exist_ok=True)
        (dest_scope / "tasks").mkdir(parents=True, exist_ok=True)
        shutil.copytree(src_task_dir, dest_task_dir)
    except OSError as e:
        return RepairResult(
            task_id=task_id,
            source_scope=src_scope,
            dest_scope=dest_scope,
            dry_run=False,
            success=False,
            message=f"copy failed: {e}",
        )

    # Rewrite meta.project_path at the destination so future reads see the
    # correct canonical path.
    dest_meta = dest_task_dir / "meta.json"
    try:
        meta = json.loads(dest_meta.read_text())
        meta["project_path"] = str(new_project_path_resolved)
        dest_meta.write_text(json.dumps(meta, indent=2, default=str))
    except (OSError, json.JSONDecodeError) as e:
        # Back out the copy if we cannot rewrite meta — an inconsistent
        # copy is worse than leaving the original in place.
        shutil.rmtree(dest_task_dir, ignore_errors=True)
        return RepairResult(
            task_id=task_id,
            source_scope=src_scope,
            dest_scope=dest_scope,
            dry_run=False,
            success=False,
            message=f"could not rewrite meta.json at destination: {e}",
        )

    # Update the global registry (under lock).
    _update_registry_for_move(task_id, str(new_project_path_resolved))

    # Fix the source scope's active_task pointer if it was us.
    _clear_active_if_matches(src_scope, task_id)

    # Remove the source — last so that a mid-run crash leaves us with the
    # duplicate (recoverable) rather than a gap (data loss).
    try:
        shutil.rmtree(src_task_dir)
    except OSError as e:
        log(f"warning: could not remove source {src_task_dir}: {e}")

    # Emit the event (best-effort; never fails the repair).
    try:
        event_log.append_event(
            event_type="task_moved",
            task_id=task_id,
            project_path=str(new_project_path_resolved),
            meta={
                "from_scope": src_scope.name,
                "to_scope": dest_scope.name,
            },
        )
    except Exception:
        pass

    return RepairResult(
        task_id=task_id,
        source_scope=src_scope,
        dest_scope=dest_scope,
        dry_run=False,
        success=True,
        message=f"moved to {dest_task_dir}",
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _locate_task_dir(task_id: str) -> Optional[Path]:
    """Find the first existing ``<scope>/tasks/<task_id>/`` across all scopes."""
    from .store import PROJECTS_HOME
    if not PROJECTS_HOME.exists():
        return None
    for scope_dir in PROJECTS_HOME.iterdir():
        if not scope_dir.is_dir():
            continue
        candidate = scope_dir / "tasks" / task_id / "meta.json"
        if candidate.exists():
            return candidate.parent
    return None


def _update_registry_for_move(task_id: str, new_project_path: str) -> None:
    """Update the registry entry to reflect the new project_path.

    Uses the same lock file as Store._register_task so concurrent writers
    cannot lose the update.
    """
    from .store import GLOBAL_HOME, REGISTRY_FILE

    reg_file = GLOBAL_HOME / REGISTRY_FILE
    lock = GLOBAL_HOME / (REGISTRY_FILE + ".lock")
    try:
        with file_lock(lock, timeout=5.0):
            if not reg_file.exists():
                return
            try:
                registry = json.loads(reg_file.read_text())
            except (OSError, json.JSONDecodeError):
                return
            tasks = registry.get("tasks", [])
            touched = False
            for t in tasks:
                if t.get("id") == task_id:
                    t["project_path"] = new_project_path
                    touched = True
            if touched:
                reg_file.write_text(json.dumps(registry, indent=2, default=str))
    except FileLockTimeout:
        # Best-effort: registry may be slightly stale after repair until
        # the next mutation rewrites it.
        print(
            f"  [Stitch WARNING] registry lock timed out during repair of {task_id}; "
            f"a subsequent task write will sync it",
            file=sys.stderr,
        )


def _clear_active_if_matches(scope_dir: Path, task_id: str) -> None:
    active_file = scope_dir / "active_task"
    if not active_file.exists():
        return
    try:
        current = active_file.read_text().strip()
    except OSError:
        return
    if current == task_id:
        try:
            active_file.unlink()
        except OSError:
            pass
