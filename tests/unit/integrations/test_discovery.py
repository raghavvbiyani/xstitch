"""Unit tests for Stitch discovery module."""

from __future__ import annotations

import json
import os
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


class TestDiscovery:
    def test_inject_into_empty_file(self, tmp_path):
        from xstitch.discovery import _inject_into_file, Stitch_SECTION_MARKER
        target = tmp_path / "CLAUDE.md"
        content = f"{Stitch_SECTION_MARKER}\nHello\n{Stitch_SECTION_MARKER}\n"
        result = _inject_into_file(target, content)
        assert result is True
        assert target.exists()
        assert Stitch_SECTION_MARKER in target.read_text()

    def test_inject_replaces_existing_section(self, tmp_path):
        from xstitch.discovery import _inject_into_file, Stitch_SECTION_MARKER
        target = tmp_path / "CLAUDE.md"
        old_content = f"# Header\n\n{Stitch_SECTION_MARKER}\nOld stuff\n{Stitch_SECTION_MARKER}\n\n# Footer\n"
        target.write_text(old_content)

        new_section = f"{Stitch_SECTION_MARKER}\nNew stuff\n{Stitch_SECTION_MARKER}\n"
        result = _inject_into_file(target, new_section)
        assert result is True

        final = target.read_text()
        assert "New stuff" in final
        assert "Old stuff" not in final
        assert "# Header" in final
        assert "# Footer" in final

    def test_inject_handles_corrupted_single_marker(self, tmp_path):
        """The bug: file with exactly 1 marker was silently ignored."""
        from xstitch.discovery import _inject_into_file, Stitch_SECTION_MARKER
        target = tmp_path / "CLAUDE.md"
        target.write_text(f"# Header\n\n{Stitch_SECTION_MARKER}\nCorrupted — missing closing marker\n")

        new_section = f"{Stitch_SECTION_MARKER}\nFixed content\n{Stitch_SECTION_MARKER}\n"
        result = _inject_into_file(target, new_section)
        assert result is True

        final = target.read_text()
        assert "Fixed content" in final
        marker_count = final.count(Stitch_SECTION_MARKER)
        assert marker_count == 2, f"Expected 2 markers, got {marker_count}"

    def test_inject_appends_to_file_without_markers(self, tmp_path):
        from xstitch.discovery import _inject_into_file, Stitch_SECTION_MARKER
        target = tmp_path / "CLAUDE.md"
        target.write_text("# Existing content\n\nSome rules here.\n")

        new_section = f"{Stitch_SECTION_MARKER}\nStitch stuff\n{Stitch_SECTION_MARKER}\n"
        result = _inject_into_file(target, new_section)
        assert result is True

        final = target.read_text()
        assert "Existing content" in final
        assert "Stitch stuff" in final

    def test_inject_creates_new_file(self, tmp_path):
        from xstitch.discovery import _inject_into_file, Stitch_SECTION_MARKER
        target = tmp_path / "NEW_FILE.md"
        assert not target.exists()

        new_section = f"{Stitch_SECTION_MARKER}\nContent\n{Stitch_SECTION_MARKER}\n"
        result = _inject_into_file(target, new_section)
        assert result is True
        assert target.exists()

    def test_mdc_has_always_apply_true(self, tmp_path):
        """Cursor .mdc rules MUST have alwaysApply: true for reliable enforcement."""
        from xstitch.discovery import inject_agent_discovery
        inject_agent_discovery(str(tmp_path))

        mdc_path = tmp_path / ".cursor" / "rules" / "stitch-context.mdc"
        assert mdc_path.exists()
        content = mdc_path.read_text()
        assert "alwaysApply: true" in content
        assert content.startswith("---\n")

    def test_instructions_are_complete_but_not_bloated(self, tmp_path):
        """Instructions must have all behavioral rules but stay under 2000 chars."""
        from xstitch.discovery import CURSORRULES_INJECTION, CLAUDE_MD_INJECTION
        assert len(CURSORRULES_INJECTION) < 2000, f"CURSORRULES too long: {len(CURSORRULES_INJECTION)}"
        assert len(CLAUDE_MD_INJECTION) < 2500, f"CLAUDE_MD too long: {len(CLAUDE_MD_INJECTION)}"

        for injection in [CLAUDE_MD_INJECTION, CURSORRULES_INJECTION]:
            assert "FAILED:" in injection, "Missing FAILED: prefix convention"
            assert "2-3 minutes" in injection, "Missing periodic push cadence"
            assert "Troubleshooting" in injection, "Missing troubleshooting section"
            assert "decision" in injection.lower(), "Missing decision push trigger"
            assert "blocker" in injection.lower(), "Missing blocker push trigger"

    def test_full_injection_creates_all_files_when_forced(self, tmp_path, fake_global):
        """force_all=True creates ALL files regardless of installed tools."""
        from xstitch.discovery import inject_agent_discovery
        import xstitch.store as store_mod
        with patch.object(store_mod, "PROJECTS_HOME", fake_global / "projects"), \
             patch.object(store_mod, "GLOBAL_HOME", fake_global):
            inject_agent_discovery(str(tmp_path), force_all=True)

        expected_files = [
            "CLAUDE.md",
            ".cursorrules",
            ".cursor/rules/stitch-context.mdc",
            ".github/copilot-instructions.md",
            "AGENTS.md",
            "GEMINI.md",
            ".windsurfrules",
            "CONVENTIONS.md",
            ".gitignore",
        ]
        for f in expected_files:
            path = tmp_path / f
            assert path.exists(), f"Missing: {f}"

    def test_selective_injection_skips_uninstalled_tools(self, tmp_path, fake_global):
        """Default injection only creates files for detected tools."""
        from xstitch.discovery import inject_agent_discovery
        import xstitch.store as store_mod
        with patch.object(store_mod, "PROJECTS_HOME", fake_global / "projects"), \
             patch.object(store_mod, "GLOBAL_HOME", fake_global), \
             patch("xstitch.discovery._get_installed_tool_names", return_value={"Cursor"}):
            inject_agent_discovery(str(tmp_path))

        assert (tmp_path / ".cursorrules").exists(), "Cursor file should be created"
        assert (tmp_path / ".cursor" / "rules" / "stitch-context.mdc").exists()
        assert not (tmp_path / ".windsurfrules").exists(), "Windsurf not installed"
        assert not (tmp_path / "GEMINI.md").exists(), "Gemini not installed"
        assert not (tmp_path / "AGENTS.md").exists(), "Codex not installed"
        assert (tmp_path / ".gitignore").exists(), "Gitignore always created"

    def test_gitignore_contains_only_stitch_dir(self, tmp_path):
        """Gitignore must only contain .stitch/ — NOT agent instruction files.

        Agent instruction files (CLAUDE.md, AGENTS.md, .cursorrules, etc.)
        must NOT be gitignored because agents need to read them at session start.
        Gitignoring them breaks agent integration (e.g. Claude Code won't load CLAUDE.md).
        """
        from xstitch.discovery import _update_gitignore, get_injected_file_paths
        _update_gitignore(tmp_path)

        gitignore = (tmp_path / ".gitignore").read_text()
        assert ".stitch/" in gitignore
        for agent_file in get_injected_file_paths():
            assert agent_file not in gitignore, (
                f"REGRESSION: {agent_file} must NOT be gitignored — agents can't read gitignored files"
            )

    def test_gitignore_idempotent(self, tmp_path):
        from xstitch.discovery import _update_gitignore, _GITIGNORE_MARKER
        _update_gitignore(tmp_path)
        _update_gitignore(tmp_path)

        content = (tmp_path / ".gitignore").read_text()
        assert content.count(_GITIGNORE_MARKER) == 2, "Gitignore section duplicated"

    def test_gitignore_preserves_existing(self, tmp_path):
        from xstitch.discovery import _update_gitignore
        gitignore = tmp_path / ".gitignore"
        gitignore.write_text("node_modules/\n*.pyc\n")

        _update_gitignore(tmp_path)

        content = gitignore.read_text()
        assert "node_modules/" in content
        assert "*.pyc" in content
        assert ".stitch/" in content

    def test_gitignore_fixes_old_bad_entries(self, tmp_path):
        """auto-setup must clean up gitignores that have agent files from older Stitch versions."""
        from xstitch.discovery import _update_gitignore, _GITIGNORE_MARKER, get_injected_file_paths
        gitignore = tmp_path / ".gitignore"
        old_bad_entries = [".stitch/"] + get_injected_file_paths()
        old_section = f"{_GITIGNORE_MARKER}\n" + "\n".join(old_bad_entries) + f"\n{_GITIGNORE_MARKER}\n"
        gitignore.write_text("node_modules/\n\n" + old_section)

        _update_gitignore(tmp_path)

        content = gitignore.read_text()
        assert "node_modules/" in content
        assert ".stitch/" in content
        for agent_file in get_injected_file_paths():
            assert agent_file not in content, (
                f"Old bad entry {agent_file} not cleaned up from gitignore"
            )
