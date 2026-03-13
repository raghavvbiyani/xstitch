"""Integration tests for Stitch data migration."""

from __future__ import annotations

from unittest.mock import patch

import pytest


@pytest.fixture
def fake_global(tmp_path):
    """Provide a fake ~/.stitch/ home for tests that need isolated global state."""
    g = tmp_path / "fake_stitch_home"
    p = g / "projects"
    p.mkdir(parents=True)
    return g


class TestMigration:
    def test_migrates_old_repo_data(self, tmp_path, fake_global):
        """Old .stitch/ in repo should be copied to ~/.stitch/projects/ (not deleted)."""
        from xstitch.store import Store, project_key

        old_stitch = tmp_path / ".stitch"
        old_tasks = old_stitch / "tasks" / "abc123"
        old_tasks.mkdir(parents=True)
        (old_tasks / "meta.json").write_text('{"id":"abc123","title":"Old task"}')
        (old_tasks / "snapshots.json").write_text("[]")
        (old_stitch / "active_task").write_text("abc123")

        with patch("xstitch.store.GLOBAL_HOME", fake_global), \
             patch("xstitch.store.PROJECTS_HOME", fake_global / "projects"):
            store = Store(str(tmp_path))
            assert (store.tasks_dir / "abc123" / "meta.json").exists()
            assert old_stitch.exists(), "Old .stitch/ should be preserved (user decides when to clean up)"

    def test_no_migration_when_new_location_has_data(self, tmp_path, fake_global):
        """Don't overwrite new data with old data."""
        from xstitch.store import Store, project_key

        old_stitch = tmp_path / ".stitch" / "tasks" / "old_task"
        old_stitch.mkdir(parents=True)
        (old_stitch / "meta.json").write_text('{"id":"old_task"}')

        key = project_key(tmp_path)
        new_tasks = fake_global / "projects" / key / "tasks" / "new_task"
        new_tasks.mkdir(parents=True)
        (new_tasks / "meta.json").write_text('{"id":"new_task"}')

        with patch("xstitch.store.GLOBAL_HOME", fake_global), \
             patch("xstitch.store.PROJECTS_HOME", fake_global / "projects"):
            store = Store(str(tmp_path))
            assert (store.tasks_dir / "new_task" / "meta.json").exists()
            assert (tmp_path / ".stitch").exists(), "Old dir should remain when new has data"

    def test_no_migration_when_old_is_empty(self, tmp_path, fake_global):
        from xstitch.store import Store
        old_stitch = tmp_path / ".stitch" / "tasks"
        old_stitch.mkdir(parents=True)

        with patch("xstitch.store.GLOBAL_HOME", fake_global), \
             patch("xstitch.store.PROJECTS_HOME", fake_global / "projects"):
            store = Store(str(tmp_path))
            assert not store.tasks_dir.exists() or not any(store.tasks_dir.iterdir())
