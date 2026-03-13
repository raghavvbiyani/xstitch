"""Integration tests for Stitch TTL-based task cleanup."""

from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from unittest.mock import patch

import pytest


class TestTTLCleanup:
    """Tests for TTL-based stale task cleanup."""

    def test_cleanup_removes_old_completed_tasks(self, tmp_path):
        """Tasks older than TTL with non-active status should be removed."""
        from xstitch.store import Store, ACTIVE_TASK_FILE
        from datetime import datetime, timezone, timedelta
        import json

        fake_global = tmp_path / "global"
        fake_global.mkdir()

        with patch("xstitch.store.GLOBAL_HOME", fake_global), \
             patch("xstitch.store.PROJECTS_HOME", fake_global / "projects"):
            store = Store(str(tmp_path))
            store.init_project()

            task = store.create_task("Old completed task")
            task_dir = store.tasks_dir / task.id
            assert task_dir.exists()

            # Directly write stale meta.json (bypass update_task which calls touch())
            old_time = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat(timespec="seconds")
            meta = json.loads((task_dir / "meta.json").read_text())
            meta["status"] = "completed"
            meta["updated_at"] = old_time
            (task_dir / "meta.json").write_text(json.dumps(meta))

            # Clear active task so cleanup can remove it
            active_file = store.local_dir / ACTIVE_TASK_FILE
            active_file.write_text("")

            now = datetime.now(timezone.utc)
            removed = store._run_ttl_cleanup(now)
            assert removed == 1
            assert not task_dir.exists()

    def test_cleanup_preserves_active_tasks(self, tmp_path):
        """Active tasks should never be cleaned up regardless of age."""
        from xstitch.store import Store
        from datetime import datetime, timezone, timedelta

        fake_global = tmp_path / "global"
        fake_global.mkdir()

        with patch("xstitch.store.GLOBAL_HOME", fake_global), \
             patch("xstitch.store.PROJECTS_HOME", fake_global / "projects"):
            store = Store(str(tmp_path))
            store.init_project()

            task = store.create_task("Active but old task")
            old_time = (datetime.now(timezone.utc) - timedelta(days=90)).isoformat(timespec="seconds")
            task.updated_at = old_time
            store.update_task(task)

            task_dir = store.tasks_dir / task.id
            assert task_dir.exists()

            now = datetime.now(timezone.utc)
            removed = store._run_ttl_cleanup(now)
            assert removed == 0
            assert task_dir.exists()

    def test_cleanup_preserves_recent_tasks(self, tmp_path):
        """Tasks updated within TTL window should not be removed."""
        from xstitch.store import Store, ACTIVE_TASK_FILE
        from datetime import datetime, timezone, timedelta

        fake_global = tmp_path / "global"
        fake_global.mkdir()

        with patch("xstitch.store.GLOBAL_HOME", fake_global), \
             patch("xstitch.store.PROJECTS_HOME", fake_global / "projects"):
            store = Store(str(tmp_path))
            store.init_project()

            task = store.create_task("Recent completed task")
            task.status = "completed"
            recent_time = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat(timespec="seconds")
            task.updated_at = recent_time
            store.update_task(task)

            active_file = store.local_dir / ACTIVE_TASK_FILE
            active_file.write_text("")

            now = datetime.now(timezone.utc)
            removed = store._run_ttl_cleanup(now)
            assert removed == 0
            assert (store.tasks_dir / task.id).exists()

    def test_cleanup_cooldown_prevents_repeated_runs(self, tmp_path):
        """Cleanup should not run again within cooldown period."""
        from xstitch.store import Store, ACTIVE_TASK_FILE
        from datetime import datetime, timezone, timedelta

        fake_global = tmp_path / "global"
        fake_global.mkdir()

        with patch("xstitch.store.GLOBAL_HOME", fake_global), \
             patch("xstitch.store.PROJECTS_HOME", fake_global / "projects"):
            store = Store(str(tmp_path))
            store.init_project()

            task = store.create_task("Stale task")
            task.status = "completed"
            old_time = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat(timespec="seconds")
            task.updated_at = old_time
            store.update_task(task)

            active_file = store.local_dir / ACTIVE_TASK_FILE
            active_file.write_text("")

            # Simulate recent cleanup run
            marker = fake_global / ".last_cleanup"
            marker.write_text(datetime.now(timezone.utc).isoformat(timespec="seconds"))

            store._maybe_run_ttl_cleanup()
            assert (store.tasks_dir / task.id).exists()
