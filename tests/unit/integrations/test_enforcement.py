"""Unit tests for Stitch enforcement module."""

from __future__ import annotations

import json
import os
from unittest.mock import patch

import pytest


class TestEnforcement:
    def test_generate_hooks_structure(self):
        from xstitch.enforcement import generate_claude_code_hooks
        hooks = generate_claude_code_hooks()
        assert "UserPromptSubmit" in hooks
        assert "Stop" in hooks
        assert len(hooks["UserPromptSubmit"]) > 0
        assert len(hooks["Stop"]) > 0

    def test_hooks_contain_import_guard(self):
        """Hooks must guard with `import xstitch` so they're no-ops on clean machines."""
        from xstitch.enforcement import generate_claude_code_hooks
        hooks = generate_claude_code_hooks()
        for event_hooks in hooks.values():
            for hook_group in event_hooks:
                for hook in hook_group["hooks"]:
                    cmd = hook["command"]
                    assert "import xstitch" in cmd, f"Missing import guard in: {cmd}"
                    assert cmd.rstrip().endswith("; true"), f"Missing '; true' safety in: {cmd}"

    def test_install_hooks_creates_settings_file(self, tmp_path):
        from xstitch.enforcement import install_claude_code_hooks
        original_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)
            result = install_claude_code_hooks()
            assert "Installed" in result

            settings = tmp_path / ".claude" / "settings.json"
            assert settings.exists()
            config = json.loads(settings.read_text())
            assert "hooks" in config
            assert "UserPromptSubmit" in config["hooks"]
        finally:
            os.chdir(original_cwd)

    def test_install_hooks_merges_with_existing(self, tmp_path):
        """Must not overwrite existing hooks from other tools."""
        from xstitch.enforcement import install_claude_code_hooks
        original_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)
            settings_dir = tmp_path / ".claude"
            settings_dir.mkdir()
            settings_file = settings_dir / "settings.json"
            settings_file.write_text(json.dumps({
                "hooks": {
                    "UserPromptSubmit": [{"hooks": [{"type": "command", "command": "echo other-tool"}]}]
                }
            }))

            install_claude_code_hooks()

            config = json.loads(settings_file.read_text())
            hooks = config["hooks"]["UserPromptSubmit"]
            assert len(hooks) >= 2, "Should have both existing and Stitch hooks"
            commands = json.dumps(hooks)
            assert "other-tool" in commands
            assert "xstitch" in commands
        finally:
            os.chdir(original_cwd)

    def test_install_hooks_idempotent(self, tmp_path):
        """Running install twice should not duplicate hooks."""
        from xstitch.enforcement import install_claude_code_hooks
        original_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)
            install_claude_code_hooks()
            install_claude_code_hooks()

            settings = tmp_path / ".claude" / "settings.json"
            config = json.loads(settings.read_text())
            hooks = config["hooks"]["UserPromptSubmit"]
            stitch_count = sum(1 for h in hooks if "xstitch" in json.dumps(h))
            assert stitch_count == 1, f"Stitch hooks duplicated: found {stitch_count}"
        finally:
            os.chdir(original_cwd)

    def test_check_claude_code_hooks_detects_missing(self, tmp_path):
        from xstitch.enforcement import check_claude_code_hooks
        original_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)
            with patch("xstitch.enforcement.Path.home", return_value=tmp_path):
                result = check_claude_code_hooks()
                assert result["status"] == "missing"
        finally:
            os.chdir(original_cwd)
