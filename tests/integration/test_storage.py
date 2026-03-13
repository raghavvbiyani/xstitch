"""Integration tests for Stitch storage relocation."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture
def fake_global(tmp_path):
    """Provide a fake ~/.stitch/ home for tests that need isolated global state."""
    g = tmp_path / "fake_stitch_home"
    p = g / "projects"
    p.mkdir(parents=True)
    return g


class TestStorageRelocation:
    def test_project_key_deterministic(self, tmp_path):
        from xstitch.store import project_key
        key1 = project_key(tmp_path)
        key2 = project_key(tmp_path)
        assert key1 == key2

    def test_project_key_includes_name_and_hash(self, tmp_path):
        from xstitch.store import project_key
        key = project_key(tmp_path)
        assert tmp_path.name in key
        assert "-" in key
        assert len(key) > len(tmp_path.name)

    def test_project_key_unique_for_different_paths(self, tmp_path):
        from xstitch.store import project_key
        dir_a = tmp_path / "project-a"
        dir_b = tmp_path / "project-b"
        dir_a.mkdir()
        dir_b.mkdir()
        assert project_key(dir_a) != project_key(dir_b)

    def test_store_uses_global_home(self, tmp_path, fake_global):
        from xstitch.store import Store
        with patch("xstitch.store.GLOBAL_HOME", fake_global), \
             patch("xstitch.store.PROJECTS_HOME", fake_global / "projects"):
            store = Store(str(tmp_path))
            assert str(fake_global) in str(store.local_dir)
            assert "projects" in str(store.local_dir)
            assert tmp_path.name in store.project_key

    def test_store_does_not_create_in_repo(self, tmp_path, fake_global):
        from xstitch.store import Store
        with patch("xstitch.store.GLOBAL_HOME", fake_global), \
             patch("xstitch.store.PROJECTS_HOME", fake_global / "projects"):
            store = Store(str(tmp_path))
            store.init_project()
            assert not (tmp_path / ".stitch").exists()
            assert store.tasks_dir.exists()

    def test_create_task_stores_outside_repo(self, tmp_path, fake_global):
        from xstitch.store import Store
        with patch("xstitch.store.GLOBAL_HOME", fake_global), \
             patch("xstitch.store.PROJECTS_HOME", fake_global / "projects"):
            store = Store(str(tmp_path))
            store.init_project()
            task = store.create_task(title="Test task")
            meta = store.tasks_dir / task.id / "meta.json"
            assert meta.exists()
            assert str(fake_global) in str(meta)
            assert not (tmp_path / ".stitch").exists()

    def test_permission_fallback_uses_repo(self, tmp_path):
        """If ~/.stitch is not writable, fall back to in-repo .stitch/."""
        from xstitch.store import Store, Stitch_DIR
        readonly_home = tmp_path / "readonly_stitch"
        with patch("xstitch.store.GLOBAL_HOME", readonly_home), \
             patch("xstitch.store.PROJECTS_HOME", readonly_home / "projects"), \
             patch("pathlib.Path.mkdir", side_effect=PermissionError("mocked")):
            store = Store(str(tmp_path))
            assert str(store.local_dir) == str(tmp_path / Stitch_DIR)

    def test_search_works_with_new_storage(self, tmp_path, fake_global):
        """Keyword search and task listing must work at the new storage location."""
        from xstitch.store import Store
        with patch("xstitch.store.GLOBAL_HOME", fake_global), \
             patch("xstitch.store.PROJECTS_HOME", fake_global / "projects"):
            store = Store(str(tmp_path))
            store.init_project()
            store.create_task(title="Database migration to PostgreSQL", objective="Move from SQLite")
            store.create_task(title="Auth flow refactor", objective="Switch to JWT")

            all_tasks = store.list_tasks(project_only=True)
            assert len(all_tasks) == 2

            results = store.search_tasks("database")
            assert len(results) >= 1
            assert any("database" in t.title.lower() for t in results)
