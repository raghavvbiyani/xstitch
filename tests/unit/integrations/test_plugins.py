"""Unit tests for Stitch entry_points plugin registry."""

from __future__ import annotations

from unittest.mock import patch, MagicMock

import pytest


class TestPluginDiscovery:
    def test_discover_all_tools_includes_builtins(self):
        from xstitch.global_setup import discover_all_tools, ALL_TOOLS
        tools = discover_all_tools()
        assert len(tools) >= len(ALL_TOOLS)
        builtin_names = {t.name for t in ALL_TOOLS}
        discovered_names = {t.name for t in tools}
        assert builtin_names.issubset(discovered_names)

    def test_entry_points_loaded_safely(self):
        from xstitch.global_setup import _load_entry_point_tools
        plugins = _load_entry_point_tools()
        assert isinstance(plugins, list)

    def test_broken_plugin_does_not_crash(self):
        from xstitch.global_setup import _load_entry_point_tools
        with patch("importlib.metadata.entry_points") as mock_eps:
            mock_ep = MagicMock()
            mock_ep.name = "broken_tool"
            mock_ep.load.side_effect = ImportError("broken")
            mock_eps.return_value.select.return_value = [mock_ep]
            plugins = _load_entry_point_tools()
            assert len(plugins) == 0

    def test_duplicate_builtin_skipped(self):
        from xstitch.global_setup import _load_entry_point_tools, ALL_TOOLS
        with patch("importlib.metadata.entry_points") as mock_eps:
            mock_ep = MagicMock()
            mock_ep.name = ALL_TOOLS[0].name
            mock_eps.return_value.select.return_value = [mock_ep]
            plugins = _load_entry_point_tools()
            assert len(plugins) == 0


class TestSkillsSupport:
    def test_cursor_has_skills(self):
        from xstitch.global_setup import ALL_TOOLS
        cursor = next(t for t in ALL_TOOLS if t.name == "Cursor")
        paths = cursor.get_skill_paths()
        assert len(paths) > 0
        assert "SKILL.md" in str(paths[0])

    def test_aider_no_skills(self):
        from xstitch.global_setup import ALL_TOOLS
        aider = next(t for t in ALL_TOOLS if t.name == "Aider")
        assert aider.inject_skills("/tmp", dry_run=True) is None
        assert aider.get_skill_paths() == []

    def test_inject_skills_dry_run(self, tmp_path):
        from xstitch.global_setup import ALL_TOOLS
        cursor = next(t for t in ALL_TOOLS if t.name == "Cursor")
        result = cursor.inject_skills(str(tmp_path), dry_run=True)
        assert "Would create" in result

    def test_inject_skills_creates_file(self, tmp_path):
        from xstitch.global_setup import ALL_TOOLS
        cursor = next(t for t in ALL_TOOLS if t.name == "Cursor")
        result = cursor.inject_skills(str(tmp_path))
        assert "Created" in result
        skill_file = tmp_path / ".cursor" / "skills" / "xstitch" / "SKILL.md"
        assert skill_file.exists()
        assert "Stitch" in skill_file.read_text()

    def test_inject_skills_idempotent(self, tmp_path):
        from xstitch.global_setup import ALL_TOOLS
        cursor = next(t for t in ALL_TOOLS if t.name == "Cursor")
        cursor.inject_skills(str(tmp_path))
        result = cursor.inject_skills(str(tmp_path))
        assert "already exists" in result
