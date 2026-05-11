"""Tests for the optional Stitch LLM-wiki scaffold."""

from __future__ import annotations

from unittest.mock import patch


def test_wiki_init_creates_schema_index_log(tmp_path):
    from xstitch.store import Store
    from xstitch import wiki

    fake_global = tmp_path / "fake_stitch_home"
    with patch("xstitch.store.GLOBAL_HOME", fake_global), \
         patch("xstitch.store.PROJECTS_HOME", fake_global / "projects"):
        store = Store(str(tmp_path))
        store.init_project()
        path = wiki.init_wiki(store)

        assert (path / "schema.md").exists()
        assert (path / "index.md").exists()
        assert (path / "log.md").exists()
        assert (path / "raw" / "README.md").exists()
        assert (path / "sources" / "README.md").exists()
        assert (path / "pages" / "project-overview.md").exists()
        assert "LLM-wiki pattern" in (path / "schema.md").read_text()


def test_wiki_log_appends_parseable_entry(tmp_path):
    from xstitch.store import Store
    from xstitch import wiki

    fake_global = tmp_path / "fake_stitch_home"
    with patch("xstitch.store.GLOBAL_HOME", fake_global), \
         patch("xstitch.store.PROJECTS_HOME", fake_global / "projects"):
        store = Store(str(tmp_path))
        store.init_project()
        wiki.init_wiki(store)
        log = wiki.append_log(store, "query", "routing", "Captured ambiguity policy.")

        text = log.read_text()
        assert "## [" in text
        assert "query | routing" in text
        assert "Captured ambiguity policy." in text
