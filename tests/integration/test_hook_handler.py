"""Integration tests for Stitch Claude Code hook handler."""

from __future__ import annotations

import io
import json
from unittest.mock import patch, MagicMock

import pytest


class TestHookHandler:
    """Tests for the Claude Code hook handler (hook-handler command)."""

    def test_hook_handler_parses_event_flag(self):
        """CLI should accept --event flag for hook-handler."""
        import argparse

        parser = argparse.ArgumentParser(prog="xstitch")
        sub = parser.add_subparsers(dest="command")
        hh_p = sub.add_parser("hook-handler")
        hh_p.add_argument(
            "--event",
            required=True,
            choices=["UserPromptSubmit", "Stop", "PreCompact", "PreCompress", "BeforeAgent", "AfterTool", "SessionEnd"],
        )

        args = parser.parse_args(["hook-handler", "--event", "UserPromptSubmit"])
        assert args.event == "UserPromptSubmit"

        args = parser.parse_args(["hook-handler", "--event", "Stop"])
        assert args.event == "Stop"

        args = parser.parse_args(["hook-handler", "--event", "PreCompact"])
        assert args.event == "PreCompact"

    def test_hook_handler_user_prompt_creates_task(self, tmp_path):
        """UserPromptSubmit hook should run auto-route and create a task."""
        from xstitch.cli import _cmd_hook_handler
        from xstitch.store import Store

        fake_global = tmp_path / "global"
        fake_global.mkdir()

        with patch("xstitch.store.GLOBAL_HOME", fake_global), \
             patch("xstitch.store.PROJECTS_HOME", fake_global / "projects"):
            store = Store(str(tmp_path))
            store.init_project()

            stdin_json = json.dumps({"prompt": "Implement a new payment gateway"})
            args = MagicMock()
            args.event = "UserPromptSubmit"

            with patch("sys.stdin", io.StringIO(stdin_json)), \
                 patch("xstitch.intelligence.auto_setup"):
                _cmd_hook_handler(store, args)

            tasks = store.list_tasks(project_only=True)
            assert len(tasks) >= 1, "Hook should have created a task via auto-route"

    def test_hook_handler_user_prompt_outputs_to_stdout(self, tmp_path, capsys):
        """UserPromptSubmit hook should output context to stdout."""
        from xstitch.cli import _cmd_hook_handler
        from xstitch.store import Store

        fake_global = tmp_path / "global"
        fake_global.mkdir()

        with patch("xstitch.store.GLOBAL_HOME", fake_global), \
             patch("xstitch.store.PROJECTS_HOME", fake_global / "projects"):
            store = Store(str(tmp_path))
            store.init_project()

            stdin_json = json.dumps({"prompt": "Build a REST API"})
            args = MagicMock()
            args.event = "UserPromptSubmit"

            with patch("sys.stdin", io.StringIO(stdin_json)), \
                 patch("xstitch.intelligence.auto_setup"):
                _cmd_hook_handler(store, args)

            captured = capsys.readouterr()
            output = json.loads(captured.out)
            assert "systemMessage" in output, "Hook JSON should contain systemMessage"
            assert "[Stitch]" in output["systemMessage"], "systemMessage should have [Stitch] prefix"
            assert "hookSpecificOutput" in output, "Hook JSON should contain hookSpecificOutput"
            hso = output["hookSpecificOutput"]
            assert hso["hookEventName"] == "UserPromptSubmit"
            assert "Stitch CONTEXT" in hso["additionalContext"], "additionalContext should have task context"

    def test_hook_handler_stop_creates_snapshot(self, tmp_path):
        """Stop hook should create a session-end snapshot."""
        from xstitch.cli import _cmd_hook_handler
        from xstitch.store import Store

        fake_global = tmp_path / "global"
        fake_global.mkdir()

        with patch("xstitch.store.GLOBAL_HOME", fake_global), \
             patch("xstitch.store.PROJECTS_HOME", fake_global / "projects"):
            store = Store(str(tmp_path))
            store.init_project()
            task = store.create_task(title="Test task", objective="test")

            args = MagicMock()
            args.event = "Stop"

            with patch("sys.stdin", io.StringIO("{}")):
                _cmd_hook_handler(store, args)

            snaps = store.get_snapshots(task.id, limit=10)
            assert any("session ended" in s.message.lower() for s in snaps)

    def test_hook_handler_precompact_creates_checkpoint_and_copies_transcript(self, tmp_path):
        """PreCompact should save a rich recovery checkpoint before compaction."""
        from xstitch.cli import _cmd_hook_handler
        from xstitch.store import Store

        fake_global = tmp_path / "global"
        fake_global.mkdir()
        transcript = tmp_path / "transcript.jsonl"
        transcript.write_text('{"role":"user","content":"research logs.txt"}\n')

        with patch("xstitch.store.GLOBAL_HOME", fake_global), \
             patch("xstitch.store.PROJECTS_HOME", fake_global / "projects"):
            store = Store(str(tmp_path))
            store.init_project()
            task = store.create_task(title="Investigate compaction", objective="test")

            args = MagicMock()
            args.event = "PreCompact"
            payload = {
                "session_id": "s1",
                "hook_event_name": "PreCompact",
                "trigger": "auto",
                "transcript_path": str(transcript),
            }

            with patch("sys.stdin", io.StringIO(json.dumps(payload))):
                _cmd_hook_handler(store, args)

            snaps = store.get_snapshots(task.id, limit=10)
            compact = [s for s in snaps if s.source == "hook-pre-compact"]
            assert compact
            assert "PRE-SUMMARIZE CHECKPOINT" in compact[-1].message
            saved_tail = compact[-1].extra.get("transcript_tail_path")
            assert saved_tail
            assert "research logs.txt" in open(saved_tail).read()

    def test_hook_handler_gemini_before_agent_outputs_additional_context(self, tmp_path, capsys):
        """Gemini BeforeAgent gets Gemini-shaped JSON context output."""
        from xstitch.cli import _cmd_hook_handler
        from xstitch.store import Store

        fake_global = tmp_path / "global"
        fake_global.mkdir()

        with patch("xstitch.store.GLOBAL_HOME", fake_global), \
             patch("xstitch.store.PROJECTS_HOME", fake_global / "projects"):
            store = Store(str(tmp_path))
            store.init_project()

            stdin_json = json.dumps({"prompt": "Build a REST API", "session_id": "g1"})
            args = MagicMock()
            args.event = "BeforeAgent"

            with patch("sys.stdin", io.StringIO(stdin_json)), \
                 patch("xstitch.intelligence.auto_setup"):
                _cmd_hook_handler(store, args)

            output = json.loads(capsys.readouterr().out)
            assert "hookSpecificOutput" in output
            assert "additionalContext" in output["hookSpecificOutput"]
            assert output["hookSpecificOutput"].get("hookEventName") is None

    def test_hook_handler_empty_stdin_no_crash(self, tmp_path):
        """Hook should handle empty stdin gracefully."""
        from xstitch.cli import _cmd_hook_handler
        from xstitch.store import Store

        fake_global = tmp_path / "global"
        fake_global.mkdir()

        with patch("xstitch.store.GLOBAL_HOME", fake_global), \
             patch("xstitch.store.PROJECTS_HOME", fake_global / "projects"):
            store = Store(str(tmp_path))
            store.init_project()

            args = MagicMock()
            args.event = "UserPromptSubmit"

            with patch("sys.stdin", io.StringIO("")):
                _cmd_hook_handler(store, args)  # should not raise

    def test_enforcement_hooks_use_hook_handler(self):
        """Enforcement hooks should use the new hook-handler command."""
        from xstitch.enforcement import generate_claude_code_hooks
        hooks = generate_claude_code_hooks()

        usp_cmd = hooks["UserPromptSubmit"][0]["hooks"][0]["command"]
        assert "hook-handler" in usp_cmd
        assert "--event UserPromptSubmit" in usp_cmd

        stop_cmd = hooks["Stop"][0]["hooks"][0]["command"]
        assert "hook-handler" in stop_cmd
        assert "--event Stop" in stop_cmd

        precompact_cmd = hooks["PreCompact"][0]["hooks"][0]["command"]
        assert "hook-handler" in precompact_cmd
        assert "--event PreCompact" in precompact_cmd
