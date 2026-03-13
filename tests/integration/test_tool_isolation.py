"""Integration tests for Stitch tool isolation."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest


class TestToolIsolation:
    """Verify each tool's injection is isolated — changing one doesn't affect another."""

    def test_json_mcp_tools_use_separate_config_files(self, tmp_path):
        """Each JsonMcpTool writes to its own config file, not shared."""
        from xstitch.global_setup import JsonMcpTool

        cursor_cfg = tmp_path / "cursor" / "mcp.json"
        gemini_cfg = tmp_path / "gemini" / "settings.json"

        cursor = JsonMcpTool("Cursor", detect_paths=[], config_path=cursor_cfg)
        gemini = JsonMcpTool("Gemini CLI", detect_paths=[], config_path=gemini_cfg)

        cursor.inject_mcp(dry_run=False)
        gemini.inject_mcp(dry_run=False)

        assert cursor_cfg.exists() and gemini_cfg.exists()
        assert cursor_cfg != gemini_cfg
        cursor_data = json.loads(cursor_cfg.read_text())
        gemini_data = json.loads(gemini_cfg.read_text())
        assert "xstitch" in cursor_data["mcpServers"]
        assert "xstitch" in gemini_data["mcpServers"]

    def test_codex_toml_injection_does_not_affect_json_tools(self, tmp_path):
        """Codex TOML injection is completely separate from JSON tool injection."""
        from xstitch.global_setup import _inject_toml_mcp, _inject_json_mcp

        toml_path = tmp_path / "config.toml"
        json_path = tmp_path / "mcp.json"

        _inject_toml_mcp("Codex", toml_path, dry_run=False)
        _inject_json_mcp("Cursor", json_path, "mcpServers", {}, dry_run=False)

        toml_content = toml_path.read_text()
        json_content = json_path.read_text()

        assert "startup_timeout_sec" in toml_content
        assert "startup_timeout_sec" not in json_content
        assert "[mcp_servers.stitch]" in toml_content
        assert "mcpServers" in json_content

    def test_claude_code_injection_does_not_affect_other_tools(self, tmp_path):
        """Claude Code's direct config editing is self-contained."""
        from xstitch.global_setup import _inject_json_mcp

        cursor_cfg = tmp_path / "mcp.json"
        _inject_json_mcp("Cursor", cursor_cfg, "mcpServers", {}, dry_run=False)
        cursor_before = cursor_cfg.read_text()

        cursor_after = cursor_cfg.read_text()
        assert cursor_before == cursor_after

    def test_aider_injection_does_not_create_mcp_config(self, tmp_path):
        """Aider should only create instruction config, never MCP."""
        from xstitch.global_setup import AiderTool
        aider = AiderTool()
        assert aider.inject_mcp(dry_run=False) is None

    def test_adding_new_tool_preserves_existing_configs(self, tmp_path):
        """Adding MCP for a new tool must not modify existing tool configs."""
        from xstitch.global_setup import _inject_json_mcp

        cursor_cfg = tmp_path / "cursor" / "mcp.json"
        _inject_json_mcp("Cursor", cursor_cfg, "mcpServers", {}, dry_run=False)
        cursor_original = cursor_cfg.read_text()

        new_tool_cfg = tmp_path / "newtool" / "mcp.json"
        _inject_json_mcp("NewTool", new_tool_cfg, "mcpServers", {}, dry_run=False)

        assert cursor_cfg.read_text() == cursor_original

    def test_json_mcp_preserves_other_servers(self, tmp_path):
        """Injecting stitch must not remove other MCP servers from the config."""
        from xstitch.global_setup import _inject_json_mcp

        cfg = tmp_path / "mcp.json"
        existing = {"mcpServers": {"other_server": {"command": "npx", "args": ["other"]}}}
        cfg.parent.mkdir(parents=True, exist_ok=True)
        cfg.write_text(json.dumps(existing))

        _inject_json_mcp("Cursor", cfg, "mcpServers", {}, dry_run=False)
        data = json.loads(cfg.read_text())

        assert "xstitch" in data["mcpServers"]
        assert "other_server" in data["mcpServers"]
        assert data["mcpServers"]["other_server"]["command"] == "npx"
