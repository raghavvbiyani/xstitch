"""Integration tests for Stitch TOML MCP injection (Codex)."""

from __future__ import annotations

from unittest.mock import patch

import pytest


class TestTomlMcpInjection:
    """Tests for TOML-based MCP injection (Codex config.toml)."""

    def test_inject_creates_new_toml_config(self, tmp_path):
        """Should create config.toml with MCP entry when file doesn't exist."""
        from xstitch.global_setup import _inject_toml_mcp
        config_path = tmp_path / ".codex" / "config.toml"

        result = _inject_toml_mcp("Codex", config_path, dry_run=False)
        assert "Created" in result
        assert config_path.exists()

        content = config_path.read_text()
        assert "[mcp_servers.stitch]" in content
        assert "xstitch.mcp_server" in content

    def test_inject_appends_to_existing_toml(self, tmp_path):
        """Should append MCP entry to existing config.toml."""
        from xstitch.global_setup import _inject_toml_mcp
        config_path = tmp_path / "config.toml"
        config_path.write_text('[model]\nprovider = "openai"\n')

        result = _inject_toml_mcp("Codex", config_path, dry_run=False)
        assert "Added" in result

        content = config_path.read_text()
        assert '[model]' in content
        assert "[mcp_servers.stitch]" in content

    def test_inject_detects_existing_entry(self, tmp_path):
        """Should detect already-registered entry (with -u flag and startup_timeout)."""
        from xstitch.global_setup import _inject_toml_mcp, PYTHON_BIN
        config_path = tmp_path / "config.toml"
        config_path.write_text(
            f'[mcp_servers.stitch]\ncommand = "{PYTHON_BIN}"\n'
            f'args = ["-u", "-m", "xstitch.mcp_server"]\n'
            f'startup_timeout_sec = 30\n'
        )

        result = _inject_toml_mcp("Codex", config_path, dry_run=False)
        assert "Already registered" in result

    def test_inject_updates_stale_entry_missing_unbuffered(self, tmp_path):
        """Should update an old entry that lacks the -u flag."""
        from xstitch.global_setup import _inject_toml_mcp, PYTHON_BIN
        config_path = tmp_path / "config.toml"
        config_path.write_text(
            f'[mcp_servers.stitch]\ncommand = "{PYTHON_BIN}"\n'
            f'args = ["-m", "xstitch.mcp_server"]\n'
        )

        result = _inject_toml_mcp("Codex", config_path, dry_run=False)
        assert "Updated" in result
        content = config_path.read_text()
        assert '"-u"' in content
        assert "startup_timeout_sec" in content

    def test_inject_dry_run_no_changes(self, tmp_path):
        """Dry run should not create any files."""
        from xstitch.global_setup import _inject_toml_mcp
        config_path = tmp_path / "config.toml"

        result = _inject_toml_mcp("Codex", config_path, dry_run=True)
        assert "Would" in result
        assert not config_path.exists()
