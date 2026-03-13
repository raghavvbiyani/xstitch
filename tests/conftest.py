"""Shared test fixtures for Stitch test suite.

These fixtures provide isolated test environments that mirror the
real Stitch storage layout (~/.stitch/projects/<key>/) without touching
the user's actual Stitch data.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture
def project_dir(tmp_path):
    """Create a minimal project directory with storage at fake ~/.stitch/projects/."""
    (tmp_path / ".git").mkdir()
    fake_global = tmp_path / "fake_global_stitch"
    fake_projects = fake_global / "projects"
    fake_projects.mkdir(parents=True)

    with patch("xstitch.store.GLOBAL_HOME", fake_global), \
         patch("xstitch.store.PROJECTS_HOME", fake_projects):
        from xstitch.store import Store, project_key
        key = project_key(tmp_path)
        data_dir = fake_projects / key / "tasks"
        data_dir.mkdir(parents=True)
    return tmp_path


@pytest.fixture
def fake_global(tmp_path):
    """Provide a fake ~/.stitch/ home for tests that need isolated global state."""
    g = tmp_path / "fake_stitch_home"
    p = g / "projects"
    p.mkdir(parents=True)
    return g


@pytest.fixture
def site_packages_dir(tmp_path):
    """Create a fake site-packages directory for editable install tests."""
    sp = tmp_path / "site-packages"
    sp.mkdir()
    return sp
