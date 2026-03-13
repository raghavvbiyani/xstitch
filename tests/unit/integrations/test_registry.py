"""Unit tests for Stitch tool registry and global setup."""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest


class TestGlobalSetup:
    def test_resolve_python_bin_returns_valid_path(self):
        from xstitch.global_setup import _resolve_python_bin
        result = _resolve_python_bin()
        assert result
        assert os.path.isfile(result) or result == "python3"

    def test_global_instructions_are_complete_and_bounded(self):
        from xstitch.global_setup import GLOBAL_INSTRUCTIONS
        assert len(GLOBAL_INSTRUCTIONS) < 2500, (
            f"Global instructions too long: {len(GLOBAL_INSTRUCTIONS)} chars"
        )
        assert "FAILED:" in GLOBAL_INSTRUCTIONS, "Missing FAILED: convention"
        assert "2-3 minutes" in GLOBAL_INSTRUCTIONS, "Missing periodic push cadence"
        assert "Troubleshooting" in GLOBAL_INSTRUCTIONS, "Missing troubleshooting"
        assert "doctor" in GLOBAL_INSTRUCTIONS, "Missing doctor reference"


class TestToolRegistryCompleteness:
    """Verify ALL_TOOLS and TOOL_REGISTRY cover all expected tools."""

    def test_all_tools_has_all_expected_tools(self):
        """ALL_TOOLS should contain entries for all supported tools."""
        from xstitch.global_setup import ALL_TOOLS
        names = {t.name for t in ALL_TOOLS}
        expected = {
            "Cursor", "Windsurf", "Zed", "Continue.dev", "Claude Code",
            "Codex", "Gemini CLI", "Copilot CLI", "Aider",
        }
        assert expected == names

    def test_registry_backward_compat(self):
        """TOOL_REGISTRY dict list should match ALL_TOOLS names exactly."""
        from xstitch.global_setup import TOOL_REGISTRY, ALL_TOOLS
        registry_names = {t["name"] for t in TOOL_REGISTRY}
        tools_names = {t.name for t in ALL_TOOLS}
        assert registry_names == tools_names

    def test_codex_has_both_mcp_and_instructions(self):
        """Codex should have MCP (TOML) AND instruction file paths."""
        from xstitch.global_setup import ALL_TOOLS, CodexTool
        codex = next(t for t in ALL_TOOLS if t.name == "Codex")
        assert isinstance(codex, CodexTool)
        assert codex.inject_mcp(dry_run=True) is not None
        assert codex.inject_instructions(dry_run=True) is not None

    def test_gemini_has_both_mcp_and_instructions(self):
        """Gemini CLI should have MCP (JSON) AND instruction file paths."""
        from xstitch.global_setup import ALL_TOOLS, JsonMcpTool
        gemini = next(t for t in ALL_TOOLS if t.name == "Gemini CLI")
        assert isinstance(gemini, JsonMcpTool)
        assert gemini.inject_mcp(dry_run=True) is not None
        assert gemini.inject_instructions(dry_run=True) is not None

    def test_copilot_has_mcp_config(self):
        """Copilot CLI should have MCP config path."""
        from xstitch.global_setup import ALL_TOOLS, JsonMcpTool
        copilot = next(t for t in ALL_TOOLS if t.name == "Copilot CLI")
        assert isinstance(copilot, JsonMcpTool)
        assert copilot.inject_mcp(dry_run=True) is not None

    def test_each_tool_is_independent_class(self):
        """Modifying one tool's class should not affect another."""
        from xstitch.global_setup import ALL_TOOLS
        types_seen = set()
        for tool in ALL_TOOLS:
            types_seen.add(type(tool).__name__)
        assert len(types_seen) >= 4, f"Expected 4+ distinct types, got: {types_seen}"
