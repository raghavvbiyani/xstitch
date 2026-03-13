"""Unit tests for Stitch BM25 engine."""

from __future__ import annotations

from unittest.mock import patch

import pytest


class TestBM25Engine:
    def test_search_empty_query(self):
        from xstitch.search.bm25 import BM25Engine
        engine = BM25Engine()
        assert engine.search("") == []

    def test_field_weights_defined(self):
        from xstitch.search.bm25 import FIELD_WEIGHTS
        assert "title" in FIELD_WEIGHTS
        assert FIELD_WEIGHTS["title"] > FIELD_WEIGHTS["snapshots"]

    def test_coarse_fields_defined(self):
        from xstitch.search.bm25 import COARSE_FIELDS
        assert "title" in COARSE_FIELDS
        assert "objective" in COARSE_FIELDS

    def test_bm25_constants(self):
        from xstitch.search.bm25 import BM25_K1, BM25_B
        assert BM25_K1 == 1.5
        assert BM25_B == 0.75

    def test_task_document_build(self, tmp_path):
        from xstitch.search.bm25 import TaskDocument
        from xstitch.store import Store
        from xstitch.models import Task

        fake_global = tmp_path / "global"
        fake_global.mkdir()

        with patch("xstitch.store.GLOBAL_HOME", fake_global), \
             patch("xstitch.store.PROJECTS_HOME", fake_global / "projects"):
            store = Store(str(tmp_path))
            store.init_project()
            task = store.create_task("Database migration", objective="Move from SQLite to PostgreSQL")

            doc = TaskDocument(task_id=task.id, task=task)
            doc.build(store)

            assert doc.total_tokens > 0
            assert len(doc.field_tokens["title"]) > 0
            title_tokens = doc.field_tokens["title"]
            assert any(t.startswith("datab") or t == "db" for t in title_tokens)

    def test_get_all_tokens(self, tmp_path):
        from xstitch.search.bm25 import BM25Engine
        from xstitch.store import Store

        fake_global = tmp_path / "global"
        fake_global.mkdir()

        with patch("xstitch.store.GLOBAL_HOME", fake_global), \
             patch("xstitch.store.PROJECTS_HOME", fake_global / "projects"):
            store = Store(str(tmp_path))
            store.init_project()
            store.create_task("Database migration")

            engine = BM25Engine()
            engine.index(store)
            tokens = engine.get_all_tokens()
            assert len(tokens) > 0
            assert any(t.startswith("datab") or t == "db" for t in tokens)
