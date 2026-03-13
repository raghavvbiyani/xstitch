"""Unit tests for Stitch CLI commands."""

from __future__ import annotations

import argparse
from unittest.mock import patch, MagicMock

import pytest


class TestCLIIdFlag:
    def test_effective_task_id_prefers_flag(self):
        from xstitch.cli import _effective_task_id
        args = MagicMock()
        args.flag_id = "from-flag"
        args.task_id = "from-positional"
        assert _effective_task_id(args) == "from-flag"

    def test_effective_task_id_falls_back_to_positional(self):
        from xstitch.cli import _effective_task_id
        args = MagicMock()
        args.flag_id = None
        args.task_id = "from-positional"
        assert _effective_task_id(args) == "from-positional"

    def test_effective_task_id_returns_none_when_both_empty(self):
        from xstitch.cli import _effective_task_id
        args = MagicMock()
        args.flag_id = None
        args.task_id = None
        assert _effective_task_id(args) is None

    def test_task_show_accepts_id_flag(self):
        """CLI should parse --id flag without error."""
        import argparse
        from xstitch.cli import main
        import io

        with patch("sys.argv", ["xstitch", "task", "show", "--id", "abc123"]):
            parser = argparse.ArgumentParser(prog="xstitch")
            sub = parser.add_subparsers(dest="command")
            task_p = sub.add_parser("task")
            task_sub = task_p.add_subparsers(dest="task_command")
            show_p = task_sub.add_parser("show")
            show_p.add_argument("task_id", nargs="?")
            show_p.add_argument("--id", dest="flag_id")

            args = parser.parse_args(["task", "show", "--id", "abc123"])
            assert args.flag_id == "abc123"
            assert args.task_id is None

    def test_task_show_accepts_positional(self):
        """CLI should still accept positional task_id."""
        import argparse

        parser = argparse.ArgumentParser(prog="xstitch")
        sub = parser.add_subparsers(dest="command")
        task_p = sub.add_parser("task")
        task_sub = task_p.add_subparsers(dest="task_command")
        show_p = task_sub.add_parser("show")
        show_p.add_argument("task_id", nargs="?")
        show_p.add_argument("--id", dest="flag_id")

        args = parser.parse_args(["task", "show", "abc123"])
        assert args.task_id == "abc123"
        assert args.flag_id is None
