"""Integration test simulating the Cursor ↔ Claude Code handoff scenario.

This is the headline regression test for the cross-agent sync feature:
reproduces the exact bug that motivated the change (task 1d6a4773d4f7
was written by Cursor with cwd=home and Claude in the repo couldn't find
it) and verifies our fix in end-to-end form.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture
def two_agents(tmp_path, monkeypatch):
    """Simulate two agents:
      - cursor: cwd = fake home (no markers)
      - claude-code: cwd = repo (has .git)

    Both share the same fake ~/.ahcp/ so the registry and event log are
    visible to both.
    """
    monkeypatch.delenv("AHCP_PROJECT_PATH", raising=False)

    fake_home = tmp_path / "fake_home"
    fake_home.mkdir()

    repo = tmp_path / "workspace" / "myrepo"
    repo.mkdir(parents=True)
    (repo / ".git").mkdir()

    fake_global = tmp_path / "fake_stitch"
    (fake_global / "projects").mkdir(parents=True)

    with patch("xstitch.store.GLOBAL_HOME", fake_global), \
         patch("xstitch.store.PROJECTS_HOME", fake_global / "projects"):
        yield {
            "fake_global": fake_global,
            "fake_home": fake_home,
            "repo": repo,
        }


class TestCursorToClaudeHandoff:

    def test_task_created_by_cursor_from_home_is_visible_to_claude_in_repo(
        self, two_agents, monkeypatch
    ):
        """The original bug: Cursor's MCP is spawned with cwd=home, so task
        ends up bucketed under the home project key. Claude's MCP has
        cwd=repo. Before the fix, ``get_task`` from Claude's store returned
        None. After the fix, read-through via the registry makes it visible.
        """
        from xstitch.store import Store

        # Cursor agent — cwd is the (fake) home directory.
        monkeypatch.chdir(two_agents["fake_home"])
        monkeypatch.setenv("AHCP_AGENT", "cursor")
        cursor_store = Store()  # no override, no env — must resolve via cwd
        task = cursor_store.create_task(
            title="Brazil Flixbus early reservation - QA bug fixes handoff",
            objective="from cursor agent in home",
        )

        # Claude agent — cwd inside the repo.
        monkeypatch.chdir(two_agents["repo"])
        monkeypatch.setenv("AHCP_AGENT", "claude-code")
        claude_store = Store()

        # Sanity: they really are different project scopes.
        assert claude_store.project_key != cursor_store.project_key

        # The bugfix: claude can read the task via registry fallback.
        found = claude_store.get_task(task.id)
        assert found is not None
        assert found.id == task.id
        assert found.title == task.title

    def test_event_log_records_cursors_write_and_claude_sees_it(
        self, two_agents, monkeypatch
    ):
        from xstitch.store import Store
        from xstitch.models import Snapshot
        from xstitch import event_log

        # ── Cursor session (writes) ──────────────────────────────────
        monkeypatch.chdir(two_agents["fake_home"])
        monkeypatch.setenv("AHCP_AGENT", "cursor")
        cursor_store = Store()
        task = cursor_store.create_task(
            title="cross agent test task", objective="cursor creates"
        )
        cursor_store.add_snapshot(
            task.id, Snapshot(message="cursor did something important", source="manual")
        )

        # ── Claude session (reads via event log) ─────────────────────
        monkeypatch.chdir(two_agents["repo"])
        monkeypatch.setenv("AHCP_AGENT", "claude-code")

        # Claude hasn't seen any events yet, so all of cursor's should show up.
        all_events = event_log.read_events(limit=100)
        assert len(all_events) >= 2  # task_created + snapshot_added
        types = {e["event_type"] for e in all_events}
        assert "task_created" in types
        assert "snapshot_added" in types
        # All events attributed to cursor.
        agents = {e["agent"] for e in all_events}
        assert "cursor" in agents

    def test_mark_seen_advances_cursor_and_filters_new_events(
        self, two_agents, monkeypatch
    ):
        from xstitch.store import Store
        from xstitch.models import Snapshot
        from xstitch import event_log

        # Cursor writes batch #1.
        monkeypatch.chdir(two_agents["fake_home"])
        monkeypatch.setenv("AHCP_AGENT", "cursor")
        cursor_store = Store()
        task = cursor_store.create_task("batch1", objective="first")
        cursor_store.add_snapshot(task.id, Snapshot(message="snap one from cursor"))

        # Claude starts up, reads everything, marks seen.
        monkeypatch.chdir(two_agents["repo"])
        monkeypatch.setenv("AHCP_AGENT", "claude-code")
        initial = event_log.read_events(limit=100)
        assert len(initial) >= 2
        event_log.set_cursor("claude-code")
        cursor = event_log.get_cursor("claude-code")
        assert cursor is not None

        # Cursor writes batch #2 *after* claude's cursor.
        import time as _time
        _time.sleep(0.02)
        monkeypatch.chdir(two_agents["fake_home"])
        monkeypatch.setenv("AHCP_AGENT", "cursor")
        cursor_store.add_snapshot(task.id, Snapshot(message="snap two from cursor"))

        # Claude queries with its cursor → only the new batch shows up.
        monkeypatch.chdir(two_agents["repo"])
        monkeypatch.setenv("AHCP_AGENT", "claude-code")
        since = cursor["ts"]
        new_events = event_log.read_events(
            filt=event_log.EventFilter(since_ts=since)
        )
        # At least the newest snapshot event is present; no duplicates
        # from before the cursor.
        messages = [e.get("meta", {}).get("message_preview", "") for e in new_events]
        assert any("snap two" in m for m in messages)
        assert not any("snap one" in m for m in messages)

    def test_decision_written_by_cursor_readable_from_claude_via_for_task(
        self, two_agents, monkeypatch
    ):
        from xstitch.store import Store
        from xstitch.models import Decision

        monkeypatch.chdir(two_agents["fake_home"])
        monkeypatch.setenv("AHCP_AGENT", "cursor")
        cursor_store = Store()
        task = cursor_store.create_task("decision cross", objective="")
        cursor_store.add_decision(
            task.id,
            Decision(
                problem="How to route cross-agent reads",
                chosen="Registry fallback + for_task",
                reasoning="Preserves one-Store=one-project invariant",
            ),
        )

        monkeypatch.chdir(two_agents["repo"])
        monkeypatch.setenv("AHCP_AGENT", "claude-code")
        claude_store = Store()

        # Direct read is cross-project.
        decs = claude_store.get_decisions(task.id)
        assert len(decs) == 1
        assert decs[0].chosen == "Registry fallback + for_task"

        # ``for_task`` gives the owning store; queries on it behave local-first.
        owner = claude_store.for_task(task.id)
        assert owner is not None
        assert owner.project_key == cursor_store.project_key
        assert owner.get_decisions(task.id)[0].chosen == "Registry fallback + for_task"
