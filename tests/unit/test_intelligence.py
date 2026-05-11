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


class TestPromptTitleOverlapHelper:
    """Unit tests for the token-overlap helper that gates matches."""

    def test_identical_prompt_and_title_is_high(self):
        from xstitch.intelligence import _prompt_title_overlap
        from xstitch.models import Task

        task = Task(id="x", title="debug redis caching latency")
        assert _prompt_title_overlap("debug redis caching latency", task) >= 0.8

    def test_domain_overlap_only_is_low(self):
        """Shared domain words with otherwise-different content scores below the cross-project gate."""
        from xstitch.intelligence import _prompt_title_overlap, CROSS_PROJECT_TITLE_OVERLAP_MIN
        from xstitch.models import Task

        task = Task(
            id="x",
            title="Brazil Flixbus early reservation QA bug fixes handoff",
        )
        prompt = (
            "Fix hasInsufficientSeatsForPassengers for aguiabranca following buson "
            "and clickbus. Bump version, follow README."
        )
        assert _prompt_title_overlap(prompt, task) < CROSS_PROJECT_TITLE_OVERLAP_MIN

    def test_empty_inputs_return_zero(self):
        from xstitch.intelligence import _prompt_title_overlap
        from xstitch.models import Task

        assert _prompt_title_overlap("", Task(id="x", title="anything")) == 0.0
        assert _prompt_title_overlap("anything", Task(id="x", title="")) == 0.0


class TestCrossProjectTitleOverlapGate:
    """Regression tests for the cross-project title-overlap gate.

    Background (the "brazil-utils collision"): a user in project A fires a
    prompt that legitimately belongs to a new local task. BM25 picks up a
    task in project B whose objective happens to mention the same domain
    vocabulary. Old behaviour was to silently clone project B's task into
    project A at a 0.40 confidence bar. New behaviour: cross-project
    matches must clear BOTH a stricter confidence bar AND a title-token
    Jaccard floor against the user's prompt.
    """

    def test_domain_vocabulary_collision_does_not_cross_clone(self, tmp_path, fake_global):
        """Prompt shares domain words with a foreign task but is a different piece of work."""
        from xstitch.intelligence import auto_route
        from xstitch.store import Store

        with patch("xstitch.store.GLOBAL_HOME", fake_global), \
             patch("xstitch.store.PROJECTS_HOME", fake_global / "projects"):
            foreign = tmp_path / "goeuro-connect-adapter-flixbus"
            foreign.mkdir()
            (foreign / ".git").mkdir()
            store_foreign = Store(str(foreign))
            store_foreign.init_project()
            store_foreign.create_task(
                title="Brazil Flixbus early reservation - QA bug fixes handoff (Cursor -> Claude)",
                objective=(
                    "Two QA-reported bugs in Brazil early-reservation flow for flixbus "
                    "fixed and isolated from European flow. buson and clickbus were "
                    "referenced as comparison points; position-per-type selector added."
                ),
                tags=["flixbus", "brazil", "early-reservation", "bug-fix", "handoff"],
            )

            local = tmp_path / "brazil-integrations-util"
            local.mkdir()
            (local / ".git").mkdir()
            store_local = Store(str(local))
            store_local.init_project()

            prompt = (
                "Fix hasInsufficientSeatsForPassengers behaviour in SeatMapAncillaryUtils "
                "so it returns true for sold out inventory. Raise a PR for aguiabranca "
                "with similar changes we already did in buson and clickbus. Follow "
                "README changelog and bump version."
            )
            result = auto_route(prompt, store_local)

            assert result["action"] != "resumed_cross_project", (
                f"Domain-vocabulary overlap must not trigger cross-project clone. "
                f"Got action={result['action']}"
            )
            assert result["action"] in ("created", "resumed")
            assert result["task"] is not None

    def test_identical_prompt_still_cross_clones(self, tmp_path, fake_global):
        """Same user running the SAME prompt in two projects must still clone."""
        from xstitch.intelligence import auto_route
        from xstitch.store import Store

        with patch("xstitch.store.GLOBAL_HOME", fake_global), \
             patch("xstitch.store.PROJECTS_HOME", fake_global / "projects"):
            proj_a = tmp_path / "proj-a"
            proj_a.mkdir()
            (proj_a / ".git").mkdir()
            store_a = Store(str(proj_a))
            store_a.init_project()
            r_a = auto_route("debug redis caching latency for inventory service", store_a)
            assert r_a["action"] == "created"

            proj_b = tmp_path / "proj-b"
            proj_b.mkdir()
            (proj_b / ".git").mkdir()
            store_b = Store(str(proj_b))
            store_b.init_project()
            r_b = auto_route("debug redis caching latency for inventory service", store_b)

            assert r_b["action"] == "resumed_cross_project"

    def test_cross_project_warning_surfaces_in_formatted_output(self, tmp_path, fake_global):
        """Cross-project clone must explicitly warn the user it crossed a project boundary."""
        from xstitch.intelligence import auto_route, format_auto_route_response
        from xstitch.store import Store

        with patch("xstitch.store.GLOBAL_HOME", fake_global), \
             patch("xstitch.store.PROJECTS_HOME", fake_global / "projects"):
            proj_a = tmp_path / "proj-a"
            proj_a.mkdir()
            (proj_a / ".git").mkdir()
            store_a = Store(str(proj_a))
            store_a.init_project()
            auto_route("implement oauth2 pkce for mobile clients", store_a)

            proj_b = tmp_path / "proj-b"
            proj_b.mkdir()
            (proj_b / ".git").mkdir()
            store_b = Store(str(proj_b))
            store_b.init_project()
            result = auto_route("implement oauth2 pkce for mobile clients", store_b)
            assert result["action"] == "resumed_cross_project"

            text = format_auto_route_response(result)
            assert "DIFFERENT project" in text
            assert "start fresh" in text or "new task" in text


class TestLocalMatchOverlapGate:
    """Regression tests for the local-match overlap gate.

    Background (the "BCR-drop collision"): a user in repo X with an active task
    titled 'Debug BCR drop in Brazil for bus mode...' typed a NEW prompt
    'analyze provider funnel data'. BM25 returned the BCR-drop task at
    confidence ~0.79 because the task's body contained common stems like
    'data' / 'drop' / 'analyz', but the prompt-vs-title literal overlap
    was 0.00. Old behaviour: silently resumed the BCR-drop task and
    injected its briefing. New behaviour: local matches must clear BOTH
    the confidence bar AND a (gentle) title-overlap floor.
    """

    def test_local_match_with_zero_title_overlap_is_rejected(self, tmp_path, fake_global):
        """High BM25 confidence + zero title overlap must NOT auto-resume."""
        from xstitch.intelligence import auto_route
        from xstitch.store import Store

        with patch("xstitch.store.GLOBAL_HOME", fake_global), \
             patch("xstitch.store.PROJECTS_HOME", fake_global / "projects"):
            store = Store(str(tmp_path))
            store.init_project()
            store.create_task(
                title="Debug BCR drop in Brazil for bus mode and arrival/departure country",
                objective=(
                    "Investigate booking conversion rate drop. Analyze data across "
                    "providers. Look at funnel metrics, sold-out reporting, error "
                    "buckets, provider share impact, soldout trend. Use ask-data "
                    "queries and KQL templates to gather evidence."
                ),
                tags=["bcr", "drop", "brazil", "bus", "data", "funnel", "analyz"],
            )

            prompt = "analyze provider funnel data"
            result = auto_route(prompt, store)

            assert result["action"] == "created", (
                f"Local match with zero title overlap must create a fresh task, "
                f"got action={result['action']}"
            )

    def test_local_match_with_meaningful_overlap_still_resumes(self, tmp_path, fake_global):
        """Genuine paraphrased prompt with literal-word overlap must still resume."""
        from xstitch.intelligence import auto_route
        from xstitch.store import Store

        with patch("xstitch.store.GLOBAL_HOME", fake_global), \
             patch("xstitch.store.PROJECTS_HOME", fake_global / "projects"):
            store = Store(str(tmp_path))
            store.init_project()
            store.create_task(
                title="Debug BCR drop in Brazil for bus mode",
                objective="Investigate booking conversion rate drop in Brazil",
                tags=["bcr", "drop", "brazil"],
            )

            result = auto_route("resume the brazil bcr drop investigation", store)
            assert result["action"] in ("resumed", "loaded_active")

    def test_active_task_fallback_blocks_unrelated_resume(self, tmp_path, fake_global):
        """'continue X' where X is not what the active task is about must NOT
        load the active task's briefing."""
        from xstitch.intelligence import auto_route
        from xstitch.store import Store

        with patch("xstitch.store.GLOBAL_HOME", fake_global), \
             patch("xstitch.store.PROJECTS_HOME", fake_global / "projects"):
            store = Store(str(tmp_path))
            store.init_project()
            store.create_task(
                title="Debug BCR drop in Brazil for bus mode and arrival/departure country",
                objective="Investigate booking conversion rate drop in Brazil for bus mode",
                tags=["bcr", "drop", "brazil", "bus"],
            )

            result = auto_route("continue oncall investigation", store)

            assert result["action"] != "loaded_active", (
                f"Resume fallback must not load briefing for an unrelated active task; "
                f"got action={result['action']}, "
                f"briefing_len={len(result.get('briefing', ''))}"
            )
            assert not result.get("briefing"), (
                "Unrelated active-task fallback must not inject a briefing"
            )

    def test_active_task_fallback_loads_when_overlap_present(self, tmp_path, fake_global):
        """When the active task IS related to a 'continue' prompt, briefing loads."""
        from xstitch.intelligence import auto_route
        from xstitch.store import Store

        with patch("xstitch.store.GLOBAL_HOME", fake_global), \
             patch("xstitch.store.PROJECTS_HOME", fake_global / "projects"):
            store = Store(str(tmp_path))
            store.init_project()
            store.create_task(
                title="Investigate oncall BCR drop in Brazil",
                objective="On-call investigation of BCR drop affecting Brazil bus traffic",
                tags=["oncall", "bcr", "brazil"],
            )

            result = auto_route("continue oncall investigation", store)
            assert result["action"] in ("resumed", "loaded_active")
            assert result.get("briefing")
