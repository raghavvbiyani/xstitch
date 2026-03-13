"""Unit tests for Stitch doctor module."""

from __future__ import annotations

import json
import os
from unittest.mock import patch

import pytest


class TestDoctor:
    def test_doctor_returns_results(self, project_dir, fake_global):
        from xstitch.doctor import run_doctor
        with patch("xstitch.store.GLOBAL_HOME", fake_global), \
             patch("xstitch.store.PROJECTS_HOME", fake_global / "projects"):
            results = run_doctor(str(project_dir))
        assert isinstance(results, list)
        assert len(results) > 0
        for r in results:
            assert "category" in r
            assert "name" in r
            assert "status" in r

    def test_doctor_detects_missing_instruction_files(self, project_dir, fake_global):
        from xstitch.doctor import run_doctor
        with patch("xstitch.store.GLOBAL_HOME", fake_global), \
             patch("xstitch.store.PROJECTS_HOME", fake_global / "projects"):
            results = run_doctor(str(project_dir))
        instruction_results = [r for r in results if r["category"] == "Instructions"]
        assert len(instruction_results) > 0
        missing = [r for r in instruction_results if r["status"] == "WARN"]
        assert len(missing) > 0

    def test_doctor_detects_unpaired_markers(self, project_dir):
        from xstitch.doctor import _check_instruction_file
        from xstitch.discovery import Stitch_SECTION_MARKER
        corrupt_file = project_dir / "CLAUDE.md"
        corrupt_file.write_text(f"# Header\n{Stitch_SECTION_MARKER}\nOnly one marker\n")
        result = _check_instruction_file("CLAUDE.md", corrupt_file)
        assert result["status"] == "WARN"
        assert "corrupted" in result["detail"].lower() or "unpaired" in result["detail"].lower()

    def test_doctor_passes_properly_injected_file(self, project_dir):
        from xstitch.doctor import _check_instruction_file
        from xstitch.discovery import Stitch_SECTION_MARKER
        good_file = project_dir / "CLAUDE.md"
        good_file.write_text(f"{Stitch_SECTION_MARKER}\nContent\n{Stitch_SECTION_MARKER}\n")
        result = _check_instruction_file("CLAUDE.md", good_file)
        assert result["status"] == "PASS"

    def test_format_doctor_report_readable(self, project_dir, fake_global):
        from xstitch.doctor import run_doctor, format_doctor_report
        with patch("xstitch.store.GLOBAL_HOME", fake_global), \
             patch("xstitch.store.PROJECTS_HOME", fake_global / "projects"):
            results = run_doctor(str(project_dir))
        report = format_doctor_report(results)
        assert "Stitch Doctor" in report
        assert "passed" in report.lower()

    def test_doctor_checks_cursor_always_apply(self, project_dir, fake_global):
        from xstitch.doctor import run_doctor
        mdc_dir = project_dir / ".cursor" / "rules"
        mdc_dir.mkdir(parents=True)
        mdc_file = mdc_dir / "stitch-context.mdc"
        mdc_file.write_text("---\ndescription: test\n---\n# No alwaysApply\n")
        with patch("xstitch.store.GLOBAL_HOME", fake_global), \
             patch("xstitch.store.PROJECTS_HOME", fake_global / "projects"):
            results = run_doctor(str(project_dir))
        cursor_checks = [r for r in results if "alwaysApply" in r["name"]]
        assert len(cursor_checks) > 0
        assert cursor_checks[0]["status"] == "WARN"
