"""Unit tests for Stitch persistent search index."""

from __future__ import annotations

import json

import pytest


class TestPersistentIndex:
    def test_save_and_load(self, tmp_path):
        from xstitch.search.index import PersistentIndex
        idx = PersistentIndex(tmp_path / "index.json")
        idx.set_entry("task1", {"tokens": ["foo"], "_mtime": 100.0})
        idx.save()

        idx2 = PersistentIndex(tmp_path / "index.json")
        assert idx2.load() is True
        assert idx2.get_entry("task1") is not None

    def test_is_stale(self, tmp_path):
        from xstitch.search.index import PersistentIndex
        idx = PersistentIndex(tmp_path / "index.json")
        idx.set_entry("task1", {"_mtime": 100.0})
        assert idx.is_stale("task1", 200.0) is True
        assert idx.is_stale("task1", 50.0) is False
        assert idx.is_stale("nonexistent", 100.0) is True

    def test_remove_entry(self, tmp_path):
        from xstitch.search.index import PersistentIndex
        idx = PersistentIndex(tmp_path / "index.json")
        idx.set_entry("task1", {"data": "test"})
        idx.remove_entry("task1")
        assert idx.get_entry("task1") is None

    def test_task_ids(self, tmp_path):
        from xstitch.search.index import PersistentIndex
        idx = PersistentIndex(tmp_path / "index.json")
        idx.set_entry("t1", {})
        idx.set_entry("t2", {})
        assert idx.task_ids() == {"t1", "t2"}

    def test_atomic_save(self, tmp_path):
        from xstitch.search.index import PersistentIndex
        idx = PersistentIndex(tmp_path / "index.json")
        idx.set_entry("task1", {"data": "test"})
        idx.save()
        assert not (tmp_path / "index.tmp").exists()
        assert (tmp_path / "index.json").exists()

    def test_handles_corrupted_file(self, tmp_path):
        path = tmp_path / "index.json"
        path.write_text("not valid json!!!")
        from xstitch.search.index import PersistentIndex
        idx = PersistentIndex(path)
        assert idx.load() is False
