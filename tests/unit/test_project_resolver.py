"""Unit tests for xstitch.project_resolver."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from xstitch.project_resolver import (
    find_project_root,
    has_ahcp_sentinel,
    resolve_project_path,
    write_ahcp_sentinel,
)


@pytest.fixture(autouse=True)
def _clear_env_and_cwd(tmp_path, monkeypatch):
    """Every test starts in a neutral tmp directory with no AHCP_PROJECT_PATH set."""
    monkeypatch.delenv("AHCP_PROJECT_PATH", raising=False)
    neutral = tmp_path / "neutral_cwd"
    neutral.mkdir()
    monkeypatch.chdir(neutral)
    yield


class TestResolveOverride:

    def test_explicit_override_wins_over_everything(self, tmp_path, monkeypatch):
        override = tmp_path / "override"
        override.mkdir()
        monkeypatch.setenv("AHCP_PROJECT_PATH", str(tmp_path / "env_path"))
        (tmp_path / "env_path").mkdir()

        result = resolve_project_path(str(override))

        assert result == override.resolve()

    def test_empty_override_falls_through(self, tmp_path, monkeypatch):
        expected = tmp_path / "from_env"
        expected.mkdir()
        monkeypatch.setenv("AHCP_PROJECT_PATH", str(expected))

        assert resolve_project_path("") == expected.resolve()
        assert resolve_project_path(None) == expected.resolve()

    def test_override_is_always_resolved(self, tmp_path):
        relative = tmp_path / "target"
        relative.mkdir()

        result = resolve_project_path(str(relative))

        assert result.is_absolute()
        assert result == relative.resolve()


class TestResolveEnvVar:

    def test_env_var_wins_over_ancestor_walk(self, tmp_path, monkeypatch):
        env_dir = tmp_path / "env_target"
        env_dir.mkdir()

        repo = tmp_path / "some_repo"
        repo.mkdir()
        (repo / ".git").mkdir()
        monkeypatch.chdir(repo)
        monkeypatch.setenv("AHCP_PROJECT_PATH", str(env_dir))

        assert resolve_project_path() == env_dir.resolve()

    def test_invalid_env_var_falls_through_to_ancestor_walk(self, tmp_path, monkeypatch):
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / ".git").mkdir()
        monkeypatch.chdir(repo)
        monkeypatch.setenv("AHCP_PROJECT_PATH", "/this/path/does/not/exist")

        assert resolve_project_path() == repo.resolve()

    def test_env_var_with_workspace_placeholder_ignored(self, tmp_path, monkeypatch):
        # Hosts that don't expand ${workspaceFolder} leak the literal string.
        # The resolver must not honor it, and must fall back.
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / ".git").mkdir()
        monkeypatch.chdir(repo)
        monkeypatch.setenv("AHCP_PROJECT_PATH", "${workspaceFolder}")

        assert resolve_project_path() == repo.resolve()

    def test_env_var_pointing_to_file_ignored(self, tmp_path, monkeypatch):
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / ".git").mkdir()
        monkeypatch.chdir(repo)

        a_file = tmp_path / "notadir"
        a_file.write_text("nope")
        monkeypatch.setenv("AHCP_PROJECT_PATH", str(a_file))

        assert resolve_project_path() == repo.resolve()


class TestAncestorWalk:

    @pytest.mark.parametrize(
        "marker_name, marker_type",
        [
            (".git", "directory"),
            (".git", "file"),  # git worktrees use a .git FILE pointing elsewhere
            (".ahcp", "directory"),
            (".ahcp", "file"),  # explicit sentinel for non-git projects
        ],
    )
    def test_finds_marker_in_current_directory(
        self, tmp_path, monkeypatch, marker_name, marker_type
    ):
        repo = tmp_path / "repo"
        repo.mkdir()
        target = repo / marker_name
        if marker_type == "directory":
            target.mkdir()
        else:
            target.write_text("gitdir: /some/where\n")
        monkeypatch.chdir(repo)

        assert resolve_project_path() == repo.resolve()

    def test_finds_marker_in_ancestor_directory(self, tmp_path, monkeypatch):
        repo = tmp_path / "repo"
        (repo / "src" / "deep" / "nested").mkdir(parents=True)
        (repo / ".git").mkdir()
        monkeypatch.chdir(repo / "src" / "deep" / "nested")

        assert resolve_project_path() == repo.resolve()

    def test_nested_repos_closest_marker_wins(self, tmp_path, monkeypatch):
        outer = tmp_path / "outer"
        (outer / ".git").mkdir(parents=True)
        inner = outer / "subproj"
        inner.mkdir()
        (inner / ".git").mkdir()
        monkeypatch.chdir(inner)

        assert resolve_project_path() == inner.resolve()

    def test_no_marker_falls_back_to_cwd(self, tmp_path, monkeypatch):
        nowhere = tmp_path / "wilderness"
        nowhere.mkdir()
        monkeypatch.chdir(nowhere)

        # Home has no .git/.ahcp in test env so walk just exhausts.
        result = resolve_project_path()

        # Should be cwd, NOT home (that's the whole point).
        assert result == nowhere.resolve()
        assert result != Path.home().resolve()

    def test_home_directory_never_auto_selected(self, tmp_path, monkeypatch):
        """Critical invariant: even if ~ has markers, a cwd inside ~ without
        its own closer marker must NOT resolve to ~."""
        fake_home = tmp_path / "fake_home"
        (fake_home / ".git").mkdir(parents=True)
        sub = fake_home / "Documents" / "random"
        sub.mkdir(parents=True)
        monkeypatch.setenv("HOME", str(fake_home))
        monkeypatch.chdir(sub)

        result = resolve_project_path()

        # We never auto-pick home; we fall through to cwd.
        assert result != fake_home.resolve()
        assert result == sub.resolve()

    def test_marker_inside_closer_non_home_ancestor_is_picked(
        self, tmp_path, monkeypatch
    ):
        fake_home = tmp_path / "fake_home"
        fake_home.mkdir()
        (fake_home / ".git").mkdir()  # would match, but we skip home

        # But a repo NOT under home must still be pickable.
        repo = tmp_path / "workspace" / "repo"
        repo.mkdir(parents=True)
        (repo / ".git").mkdir()
        sub = repo / "src"
        sub.mkdir()

        monkeypatch.setenv("HOME", str(fake_home))
        monkeypatch.chdir(sub)

        assert resolve_project_path() == repo.resolve()


class TestFindProjectRoot:

    def test_returns_none_when_no_marker(self, tmp_path):
        empty = tmp_path / "empty"
        empty.mkdir()
        assert find_project_root(empty) is None

    def test_skips_home_directory(self, tmp_path, monkeypatch):
        fake_home = tmp_path / "fake_home"
        (fake_home / ".git").mkdir(parents=True)
        monkeypatch.setenv("HOME", str(fake_home))

        # Ancestor walk from fake_home should NOT return fake_home.
        assert find_project_root(fake_home) is None


class TestSymlinks:

    def test_resolve_follows_symlinks(self, tmp_path, monkeypatch):
        real_repo = tmp_path / "real_repo"
        real_repo.mkdir()
        (real_repo / ".git").mkdir()

        link = tmp_path / "linked"
        link.symlink_to(real_repo)

        monkeypatch.chdir(link)
        result = resolve_project_path()

        # resolved to the real directory, not the symlink name
        assert result == real_repo.resolve()

    def test_broken_symlink_does_not_crash(self, tmp_path, monkeypatch):
        broken = tmp_path / "broken_link"
        broken.symlink_to(tmp_path / "does_not_exist")
        monkeypatch.setenv("AHCP_PROJECT_PATH", str(broken))

        # Path pointed to by env var is not a dir → fall back.
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / ".git").mkdir()
        monkeypatch.chdir(repo)

        # Should not raise; should fall through to ancestor walk.
        result = resolve_project_path()
        assert result == repo.resolve()


class TestSentinel:

    def test_has_ahcp_sentinel_true_for_dir(self, tmp_path):
        (tmp_path / ".ahcp").mkdir()
        assert has_ahcp_sentinel(tmp_path) is True

    def test_has_ahcp_sentinel_true_for_file(self, tmp_path):
        (tmp_path / ".ahcp").write_text("pinned")
        assert has_ahcp_sentinel(tmp_path) is True

    def test_has_ahcp_sentinel_false_when_absent(self, tmp_path):
        assert has_ahcp_sentinel(tmp_path) is False

    def test_write_sentinel_creates_file(self, tmp_path):
        sentinel = write_ahcp_sentinel(tmp_path)

        assert sentinel.exists()
        assert sentinel.is_file()
        assert "Stitch" in sentinel.read_text()

    def test_write_sentinel_is_idempotent_when_file_exists(self, tmp_path):
        existing = tmp_path / ".ahcp"
        existing.write_text("user content")

        result = write_ahcp_sentinel(tmp_path)

        assert result == existing
        assert existing.read_text() == "user content"  # not overwritten

    def test_write_sentinel_leaves_existing_dir_alone(self, tmp_path):
        """If the user already has a ``.ahcp/`` storage directory, we must
        not overwrite it with a file."""
        existing_dir = tmp_path / ".ahcp"
        existing_dir.mkdir()
        (existing_dir / "tasks").mkdir()

        result = write_ahcp_sentinel(tmp_path)

        assert result == existing_dir
        assert existing_dir.is_dir()
        assert (existing_dir / "tasks").is_dir()


class TestIntegrationWithStore:
    """End-to-end check that Store picks up the resolver changes."""

    def test_store_uses_ancestor_walk_when_cwd_is_nested(self, tmp_path, monkeypatch):
        repo = tmp_path / "repo"
        (repo / "src").mkdir(parents=True)
        (repo / ".git").mkdir()
        monkeypatch.chdir(repo / "src")

        fake_global = tmp_path / "fake_global"
        (fake_global / "projects").mkdir(parents=True)
        with patch("xstitch.store.GLOBAL_HOME", fake_global), \
             patch("xstitch.store.PROJECTS_HOME", fake_global / "projects"):
            from xstitch.store import Store, project_key
            store = Store()

        assert store.project_path == repo.resolve()
        assert store.project_key == project_key(repo.resolve())

    def test_store_respects_env_var_pointing_at_repo(self, tmp_path, monkeypatch):
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / ".git").mkdir()
        # cwd is home but env points to repo
        monkeypatch.setenv("AHCP_PROJECT_PATH", str(repo))
        outside = tmp_path / "somewhere_else"
        outside.mkdir()
        monkeypatch.chdir(outside)

        fake_global = tmp_path / "fake_global"
        (fake_global / "projects").mkdir(parents=True)
        with patch("xstitch.store.GLOBAL_HOME", fake_global), \
             patch("xstitch.store.PROJECTS_HOME", fake_global / "projects"):
            from xstitch.store import Store
            store = Store()

        assert store.project_path == repo.resolve()
