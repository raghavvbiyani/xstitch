"""Unit and integration tests for ``xstitch.repair`` (orphan detection + move)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture
def repair_env(tmp_path):
    """Set up an isolated Stitch global home with two project scopes."""
    fake_global = tmp_path / "fake_stitch"
    (fake_global / "projects").mkdir(parents=True)

    home_proj = tmp_path / "fake_home"
    home_proj.mkdir()

    repo_proj = tmp_path / "workspace" / "myrepo"
    repo_proj.mkdir(parents=True)
    (repo_proj / ".git").mkdir()

    with patch("xstitch.store.GLOBAL_HOME", fake_global), \
         patch("xstitch.store.PROJECTS_HOME", fake_global / "projects"):
        yield {
            "fake_global": fake_global,
            "home": home_proj,
            "repo": repo_proj,
        }


# ---------------------------------------------------------------------------
# scan_orphans
# ---------------------------------------------------------------------------


class TestScanOrphans:

    def test_no_orphans_when_everything_matches(self, repair_env):
        from xstitch.store import Store
        from xstitch import repair

        repo_store = Store(str(repair_env["repo"]))
        repo_store.create_task("healthy task", objective="")

        orphans = repair.scan_orphans()
        assert orphans == []

    def test_detects_scope_mismatch(self, repair_env):
        """A task whose stored scope dir doesn't match meta.project_path is
        the classic Cursor/Claude split-brain orphan."""
        from xstitch.store import Store, PROJECTS_HOME, project_key
        from xstitch import repair

        # Create task correctly under home scope.
        home_store = Store(str(repair_env["home"]))
        task = home_store.create_task("orphan-me", objective="")

        # Manually rewrite meta.project_path to point at the repo (simulates
        # Cursor's MCP having created the task while logically belonging to
        # the repo but spawned from cwd=home).
        meta_file = (
            PROJECTS_HOME
            / project_key(repair_env["home"].resolve())
            / "tasks" / task.id / "meta.json"
        )
        meta = json.loads(meta_file.read_text())
        meta["project_path"] = str(repair_env["repo"].resolve())
        meta_file.write_text(json.dumps(meta))

        orphans = repair.scan_orphans()
        assert len(orphans) == 1
        assert orphans[0].task_id == task.id
        assert orphans[0].reason == "scope_mismatch"
        assert orphans[0].suggested_project_path == str(repair_env["repo"].resolve())

    def test_detects_no_marker(self, repair_env):
        """A task whose meta.project_path no longer points at a valid repo."""
        from xstitch.store import Store, PROJECTS_HOME, project_key
        from xstitch import repair

        ghost = repair_env["fake_global"].parent / "vanished"
        ghost.mkdir()
        ghost_store = Store(str(ghost))
        task = ghost_store.create_task("ghost task", objective="")

        # Destroy the project directory after task creation.
        import shutil
        shutil.rmtree(ghost)

        orphans = repair.scan_orphans()
        my = [o for o in orphans if o.task_id == task.id]
        assert my, "expected to detect the vanished-project task"
        assert my[0].reason == "no_marker"


# ---------------------------------------------------------------------------
# repair_orphan
# ---------------------------------------------------------------------------


class TestRepairOrphan:

    def test_repair_moves_task_to_correct_scope(self, repair_env):
        from xstitch.store import Store, PROJECTS_HOME, project_key
        from xstitch import repair

        home_store = Store(str(repair_env["home"]))
        task = home_store.create_task("move me", objective="target = repo")

        src_task_dir = (
            PROJECTS_HOME / project_key(repair_env["home"].resolve())
            / "tasks" / task.id
        )
        assert src_task_dir.exists()

        result = repair.repair_orphan(
            task_id=task.id,
            new_project_path=str(repair_env["repo"]),
        )
        assert result.success, result.message

        # Source is gone, dest exists.
        assert not src_task_dir.exists()
        dest_task_dir = (
            PROJECTS_HOME / project_key(repair_env["repo"].resolve())
            / "tasks" / task.id
        )
        assert dest_task_dir.exists()

        # meta.project_path rewritten.
        meta = json.loads((dest_task_dir / "meta.json").read_text())
        assert meta["project_path"] == str(repair_env["repo"].resolve())

        # A new Store rooted at the repo sees the task locally (no read-through needed).
        repo_store = Store(str(repair_env["repo"]))
        assert repo_store.task_is_local(task.id)
        assert repo_store.get_task(task.id).title == "move me"

    def test_repair_updates_global_registry(self, repair_env):
        from xstitch.store import Store, GLOBAL_HOME
        from xstitch import repair

        home_store = Store(str(repair_env["home"]))
        task = home_store.create_task("registry test", objective="")

        repair.repair_orphan(
            task_id=task.id,
            new_project_path=str(repair_env["repo"]),
        )

        reg = json.loads((GLOBAL_HOME / "registry.json").read_text())
        entry = next(t for t in reg["tasks"] if t["id"] == task.id)
        assert entry["project_path"] == str(repair_env["repo"].resolve())

    def test_dry_run_does_not_touch_disk(self, repair_env):
        from xstitch.store import Store, PROJECTS_HOME, project_key
        from xstitch import repair

        home_store = Store(str(repair_env["home"]))
        task = home_store.create_task("dry run test", objective="")

        src_task_dir = (
            PROJECTS_HOME / project_key(repair_env["home"].resolve())
            / "tasks" / task.id
        )
        before_snapshot = {
            "meta": (src_task_dir / "meta.json").read_text(),
            "exists": src_task_dir.exists(),
        }

        result = repair.repair_orphan(
            task_id=task.id,
            new_project_path=str(repair_env["repo"]),
            dry_run=True,
        )
        assert result.success
        assert result.dry_run is True

        # Nothing moved.
        assert src_task_dir.exists()
        assert (src_task_dir / "meta.json").read_text() == before_snapshot["meta"]
        dest_task_dir = (
            PROJECTS_HOME / project_key(repair_env["repo"].resolve())
            / "tasks" / task.id
        )
        assert not dest_task_dir.exists()

    def test_repair_aborts_when_destination_exists(self, repair_env):
        from xstitch.store import Store, PROJECTS_HOME, project_key
        from xstitch import repair

        home_store = Store(str(repair_env["home"]))
        task = home_store.create_task("duplicate", objective="")

        # Pre-create a colliding task at the destination.
        dest_scope = PROJECTS_HOME / project_key(repair_env["repo"].resolve())
        (dest_scope / "tasks" / task.id).mkdir(parents=True)
        (dest_scope / "tasks" / task.id / "meta.json").write_text("{}")

        result = repair.repair_orphan(
            task_id=task.id,
            new_project_path=str(repair_env["repo"]),
        )
        assert not result.success
        assert "already exists" in result.message

        # Original still exists untouched.
        src_task_dir = (
            PROJECTS_HOME / project_key(repair_env["home"].resolve())
            / "tasks" / task.id
        )
        assert src_task_dir.exists()

    def test_repair_emits_task_moved_event(self, repair_env):
        from xstitch.store import Store
        from xstitch import repair, event_log

        home_store = Store(str(repair_env["home"]))
        task = home_store.create_task("event me", objective="")

        repair.repair_orphan(
            task_id=task.id,
            new_project_path=str(repair_env["repo"]),
        )

        events = event_log.read_events(
            filt=event_log.EventFilter(event_types=["task_moved"])
        )
        assert any(e["task_id"] == task.id for e in events)

    def test_repair_unknown_task_id_returns_failure(self, repair_env):
        from xstitch import repair
        result = repair.repair_orphan("nope-does-not-exist", str(repair_env["repo"]))
        assert not result.success
        assert "not found" in result.message

    def test_repair_clears_active_task_pointer(self, repair_env):
        from xstitch.store import Store, PROJECTS_HOME, project_key
        from xstitch import repair

        home_store = Store(str(repair_env["home"]))
        task = home_store.create_task("active to be moved", objective="")

        scope_dir = PROJECTS_HOME / project_key(repair_env["home"].resolve())
        active_file = scope_dir / "active_task"
        assert active_file.exists()
        assert active_file.read_text().strip() == task.id

        repair.repair_orphan(
            task_id=task.id,
            new_project_path=str(repair_env["repo"]),
        )
        # Source's active_task pointer is cleared because the task it
        # named no longer lives there.
        assert not active_file.exists()


# ---------------------------------------------------------------------------
# End-to-end: doctor --repair on a real orphan layout.
# ---------------------------------------------------------------------------


class TestDoctorRepairIntegration:

    def test_doctor_reports_orphans(self, repair_env):
        from xstitch.doctor import run_doctor
        from xstitch.store import Store, PROJECTS_HOME, project_key

        home_store = Store(str(repair_env["home"]))
        task = home_store.create_task("visible to doctor", objective="")

        # Create scope mismatch by rewriting meta.project_path.
        meta_file = (
            PROJECTS_HOME / project_key(repair_env["home"].resolve())
            / "tasks" / task.id / "meta.json"
        )
        meta = json.loads(meta_file.read_text())
        meta["project_path"] = str(repair_env["repo"].resolve())
        meta_file.write_text(json.dumps(meta))

        results = run_doctor(str(repair_env["repo"]))
        orphan_checks = [r for r in results if r["name"] == "Orphaned tasks"]
        assert orphan_checks
        assert orphan_checks[0]["status"] == "WARN"
        assert "1" in orphan_checks[0]["detail"]

    def test_after_repair_doctor_reports_clean(self, repair_env):
        from xstitch.doctor import run_doctor
        from xstitch.store import Store, PROJECTS_HOME, project_key
        from xstitch import repair

        home_store = Store(str(repair_env["home"]))
        task = home_store.create_task("clean me", objective="")

        meta_file = (
            PROJECTS_HOME / project_key(repair_env["home"].resolve())
            / "tasks" / task.id / "meta.json"
        )
        meta = json.loads(meta_file.read_text())
        meta["project_path"] = str(repair_env["repo"].resolve())
        meta_file.write_text(json.dumps(meta))

        # Repair, then doctor should be clean.
        repair.repair_orphan(task.id, str(repair_env["repo"]))

        results = run_doctor(str(repair_env["repo"]))
        orphan_checks = [r for r in results if r["name"] == "Orphaned tasks"]
        assert orphan_checks
        assert orphan_checks[0]["status"] == "PASS"
