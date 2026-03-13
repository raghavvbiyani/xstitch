"""Unit tests for Stitch intelligence module."""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest


@pytest.fixture
def fake_global(tmp_path):
    """Provide a fake ~/.stitch/ home for tests that need isolated global state."""
    g = tmp_path / "fake_stitch_home"
    p = g / "projects"
    p.mkdir(parents=True)
    return g


class TestIntelligence:
    def test_workspace_root_from_env(self, tmp_path):
        from xstitch.intelligence import _get_workspace_root
        with patch.dict(os.environ, {"Stitch_WORKSPACE_ROOT": str(tmp_path)}):
            result = _get_workspace_root("/some/project")
            assert result == str(tmp_path)

    def test_workspace_root_from_project_parent(self):
        from xstitch.intelligence import _get_workspace_root
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("Stitch_WORKSPACE_ROOT", None)
            result = _get_workspace_root("/Users/dev/projects/my-app")
            assert result == "/Users/dev/projects"

    def test_workspace_root_ignores_invalid_env(self):
        from xstitch.intelligence import _get_workspace_root
        with patch.dict(os.environ, {"Stitch_WORKSPACE_ROOT": "/nonexistent/path"}):
            result = _get_workspace_root("/Users/dev/projects/my-app")
            assert result == "/Users/dev/projects"

    def test_auto_setup_runs_health_check(self, tmp_path, fake_global):
        from xstitch.intelligence import auto_setup
        (tmp_path / ".git").mkdir(exist_ok=True)
        with patch("xstitch.store.GLOBAL_HOME", fake_global), \
             patch("xstitch.store.PROJECTS_HOME", fake_global / "projects"):
            result = auto_setup(str(tmp_path), quiet=True)
        assert "health" in result


class TestConversationalDetection:
    """Verify _is_conversational helper for greeting/filler prompts."""

    def test_greeting_detected(self):
        from xstitch.intelligence import _is_conversational
        assert _is_conversational("hi") is True
        assert _is_conversational("hello") is True
        assert _is_conversational("Hi Claude") is True
        assert _is_conversational("thanks") is True
        assert _is_conversational("ok") is True
        assert _is_conversational("") is True

    def test_task_prompt_not_conversational(self):
        from xstitch.intelligence import _is_conversational
        assert _is_conversational("implement the todo app") is False
        assert _is_conversational("fix the database migration bug") is False
        assert _is_conversational("add a new endpoint for users") is False

    def test_detect_intent_unchanged(self):
        from xstitch.intelligence import detect_intent
        assert detect_intent("resume the database migration") == "resume"
        assert detect_intent("build a new REST API") == "new"
        assert detect_intent("fix the login page") == "ambiguous"


class TestAutoRouteRelevanceGating:
    """Core principle: context loading is ALWAYS gated by BM25 relevance.

    No prompt — whether a greeting, an unrelated work request, or anything
    else — should trigger full task context loading unless there is a
    relevance match between the prompt and persisted task data.
    """

    def test_greeting_with_active_task_no_briefing(self, tmp_path, fake_global):
        """'hi claude' with an active task → mention task, no briefing."""
        from xstitch.intelligence import auto_route
        from xstitch.store import Store

        with patch("xstitch.store.GLOBAL_HOME", fake_global), \
             patch("xstitch.store.PROJECTS_HOME", fake_global / "projects"):
            store = Store(str(tmp_path))
            store.init_project()
            store.create_task(title="Some existing task", objective="test")

            result = auto_route("hi claude", store)

            assert result["action"] == "active_task_exists"
            assert result["task"] is not None
            assert result["briefing"] == "", \
                "No relevance match → no briefing loaded"

    def test_greeting_without_active_task(self, tmp_path, fake_global):
        """'hello' with no tasks at all → no action."""
        from xstitch.intelligence import auto_route
        from xstitch.store import Store

        with patch("xstitch.store.GLOBAL_HOME", fake_global), \
             patch("xstitch.store.PROJECTS_HOME", fake_global / "projects"):
            store = Store(str(tmp_path))
            store.init_project()

            result = auto_route("hello", store)

            assert result["action"] == "greeting"
            assert result["task"] is None
            assert result["briefing"] == ""

    def test_unrelated_work_prompt_no_briefing(self, tmp_path, fake_global):
        """'fix the login bug' while active task is about database migration
        → mention active task exists, do NOT load its briefing."""
        from xstitch.intelligence import auto_route
        from xstitch.store import Store

        with patch("xstitch.store.GLOBAL_HOME", fake_global), \
             patch("xstitch.store.PROJECTS_HOME", fake_global / "projects"):
            store = Store(str(tmp_path))
            store.init_project()
            store.create_task(
                title="Database migration to PostgreSQL",
                objective="Migrate from SQLite",
            )

            result = auto_route("what is the weather today", store)

            assert result["action"] in ("active_task_exists", "created")
            if result["action"] == "active_task_exists":
                assert result["briefing"] == "", \
                    "Unrelated prompt → no briefing even though active task exists"

    def test_related_prompt_loads_context(self, tmp_path, fake_global):
        """'resume the database migration' with matching task → loads context."""
        from xstitch.intelligence import auto_route
        from xstitch.store import Store

        with patch("xstitch.store.GLOBAL_HOME", fake_global), \
             patch("xstitch.store.PROJECTS_HOME", fake_global / "projects"):
            store = Store(str(tmp_path))
            store.init_project()
            store.create_task(
                title="Database migration to PostgreSQL",
                objective="Migrate from SQLite to PostgreSQL using Alembic",
            )

            result = auto_route("resume the database migration", store)

            assert result["action"] in ("resumed", "loaded_active")
            assert result["task"] is not None
            assert result["task"].title == "Database migration to PostgreSQL"

    def test_explicit_new_always_creates(self, tmp_path, fake_global):
        """'build a new REST API' → creates new task regardless of active."""
        from xstitch.intelligence import auto_route
        from xstitch.store import Store

        with patch("xstitch.store.GLOBAL_HOME", fake_global), \
             patch("xstitch.store.PROJECTS_HOME", fake_global / "projects"):
            store = Store(str(tmp_path))
            store.init_project()
            store.create_task(title="Old task", objective="old")

            result = auto_route("build a new REST API for user management", store)

            assert result["action"] == "created"
            assert "REST API" in result["task"].title or "rest" in result["task"].title.lower()
