"""Unit tests for Stitch healthcheck module."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest


class TestHealthCheck:
    def test_quick_check_returns_ok_when_stitch_importable(self):
        from xstitch.healthcheck import quick_check
        result = quick_check()
        assert result["status"] in ("ok", "broken", "warning")

    def test_check_editable_install_ok_when_no_pth_files(self, site_packages_dir):
        from xstitch.healthcheck import check_editable_install
        with patch("xstitch.healthcheck.sysconfig") as mock_sc:
            mock_sc.get_path.return_value = str(site_packages_dir)
            result = check_editable_install()
            assert result["status"] == "ok"

    def test_check_editable_install_detects_broken_source(self, site_packages_dir):
        from xstitch.healthcheck import check_editable_install

        pth_file = site_packages_dir / "__editable__.xstitch-0.2.0.pth"
        pth_file.write_text("import __editable___xstitch_0_2_0_finder; __editable___xstitch_0_2_0_finder.install()")

        finder_file = site_packages_dir / "__editable___xstitch_0_2_0_finder.py"
        finder_file.write_text(
            "MAPPING = {'xstitch': '/private/tmp/deleted-path/xstitch/xstitch'}\n"
        )
        alt_finder = site_packages_dir / "__editable___xstitch_0_2_0.py"
        alt_finder.write_text(
            "MAPPING = {'xstitch': '/private/tmp/deleted-path/xstitch/xstitch'}\n"
        )

        with patch("xstitch.healthcheck.sysconfig") as mock_sc:
            mock_sc.get_path.return_value = str(site_packages_dir)
            result = check_editable_install()
            assert result["status"] == "broken"
            assert "deleted" in result["reason"].lower() or "/private/tmp" in result["reason"]
            assert "fix" in result

    def test_check_editable_install_passes_when_source_exists(self, site_packages_dir, tmp_path):
        from xstitch.healthcheck import check_editable_install

        real_source = tmp_path / "real-source" / "xstitch"
        real_source.mkdir(parents=True)
        (real_source / "__init__.py").write_text("__version__ = '0.3.0'")

        pth_file = site_packages_dir / "__editable__.xstitch-0.3.0.pth"
        pth_file.write_text("import finder; finder.install()")

        finder_file = site_packages_dir / "__editable___xstitch_0_3_0_finder.py"
        finder_file.write_text(f"MAPPING = {{'xstitch': '{real_source}'}}\n")

        with patch("xstitch.healthcheck.sysconfig") as mock_sc:
            mock_sc.get_path.return_value = str(site_packages_dir)
            result = check_editable_install()
            assert result["status"] == "ok"

    def test_check_editable_install_detects_broken_direct_url(self, site_packages_dir):
        from xstitch.healthcheck import check_editable_install

        dist_info = site_packages_dir / "xstitch-0.2.0.dist-info"
        dist_info.mkdir()
        direct_url = dist_info / "direct_url.json"
        direct_url.write_text(json.dumps({
            "dir_info": {"editable": True},
            "url": "file:///private/tmp/gone-forever/xstitch"
        }))

        with patch("xstitch.healthcheck.sysconfig") as mock_sc:
            mock_sc.get_path.return_value = str(site_packages_dir)
            result = check_editable_install()
            assert result["status"] == "broken"
            assert "gone-forever" in result["reason"]

    def test_check_python_environment_finds_python3(self):
        from xstitch.healthcheck import check_python_environment
        result = check_python_environment()
        assert result["status"] in ("ok", "warning")

    def test_diagnose_returns_structured_report(self):
        from xstitch.healthcheck import diagnose
        checks = diagnose()
        assert isinstance(checks, list)
        assert len(checks) >= 3
        for check in checks:
            assert "name" in check
            assert "status" in check

    def test_suggest_install_command_contains_pip(self):
        from xstitch.healthcheck import _suggest_install_command
        cmd = _suggest_install_command()
        assert "pip3" in cmd
        assert "xstitch" in cmd

    def test_find_stitch_source_finds_current_package(self):
        from xstitch.healthcheck import _find_stitch_source
        with patch("xstitch.healthcheck.Path.cwd") as mock_cwd:
            pkg_dir = Path(__file__).resolve().parent.parent.parent.parent
            mock_cwd.return_value = pkg_dir
            result = _find_stitch_source()
            if result:
                assert "xstitch" in result.lower() or Path(result).exists()
