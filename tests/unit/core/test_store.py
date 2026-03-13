"""Unit tests for Stitch store module."""

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


class TestStoreErrorHandling:
    def test_read_json_handles_corrupted_meta_file(self, tmp_path):
        """Files with 'meta' in name return {} on corruption, others return []."""
        from xstitch.store import Store
        corrupt = tmp_path / "meta.json"
        corrupt.write_text("{invalid json content!!!")

        result = Store._read_json(corrupt)
        assert result == {}

    def test_read_json_handles_corrupted_list_file(self, tmp_path):
        from xstitch.store import Store
        corrupt = tmp_path / "snapshots.json"
        corrupt.write_text("not json at all")

        result = Store._read_json(corrupt)
        assert result == []

    def test_read_json_handles_missing_file(self, tmp_path):
        from xstitch.store import Store
        missing = tmp_path / "nonexistent.json"

        result = Store._read_json(missing)
        assert result in ({}, [])

    def test_write_json_atomic(self, tmp_path):
        from xstitch.store import Store
        target = tmp_path / "test.json"
        data = {"key": "value", "number": 42}
        Store._write_json(target, data)

        assert target.exists()
        loaded = json.loads(target.read_text())
        assert loaded == data
        assert not (tmp_path / "test.tmp").exists()

    def test_registry_survives_corruption(self, tmp_path, fake_global):
        from xstitch.store import Store, REGISTRY_FILE
        with patch("xstitch.store.GLOBAL_HOME", fake_global), \
             patch("xstitch.store.PROJECTS_HOME", fake_global / "projects"):
            store = Store(str(tmp_path))
            reg_file = fake_global / REGISTRY_FILE
            reg_file.write_text("THIS IS NOT JSON")
            registry = store._load_registry()
            assert registry == {"tasks": []}
