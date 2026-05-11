"""Unit tests for xstitch.event_log."""

from __future__ import annotations

import json
import multiprocessing
import os
import time
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture
def fake_home(tmp_path):
    """Patch GLOBAL_HOME so event log writes to an isolated dir."""
    g = tmp_path / "fake_ahcp_home"
    g.mkdir(parents=True)
    with patch("xstitch.store.GLOBAL_HOME", g), \
         patch("xstitch.store.PROJECTS_HOME", g / "projects"):
        yield g


class TestAppendRead:

    def test_append_then_read_roundtrip(self, fake_home):
        from xstitch import event_log

        ev = event_log.append_event(
            event_type=event_log.EVENT_TASK_CREATED,
            task_id="t1",
            project_path="/proj",
            meta={"title": "hello"},
            agent="cursor",
        )
        assert ev is not None
        assert ev["event_type"] == "task_created"
        assert ev["task_id"] == "t1"
        assert ev["agent"] == "cursor"
        assert ev["seq"] >= 1
        assert "ts" in ev

        events = event_log.read_events()
        assert len(events) == 1
        assert events[0]["task_id"] == "t1"

    def test_multiple_events_preserve_seq_order(self, fake_home):
        from xstitch import event_log

        for i in range(5):
            event_log.append_event(
                event_type=event_log.EVENT_SNAPSHOT_ADDED,
                task_id=f"t{i}",
                project_path="/proj",
            )

        events = event_log.read_events()
        seqs = [e["seq"] for e in events]
        assert seqs == sorted(seqs)
        assert len(seqs) == 5
        # Seqs are monotonically increasing.
        assert all(seqs[i] < seqs[i + 1] for i in range(len(seqs) - 1))


class TestFilters:

    def test_filter_by_since_ts(self, fake_home):
        from xstitch import event_log

        event_log.append_event("task_created", "old", "/proj")
        time.sleep(0.01)
        cutoff = event_log._iso_now()
        time.sleep(0.01)
        event_log.append_event("task_created", "new", "/proj")

        events = event_log.read_events(filt=event_log.EventFilter(since_ts=cutoff))
        ids = [e["task_id"] for e in events]
        assert ids == ["new"]

    def test_filter_by_project_path(self, fake_home):
        from xstitch import event_log

        event_log.append_event("task_created", "t1", "/proj/a")
        event_log.append_event("task_created", "t2", "/proj/b")
        event_log.append_event("task_created", "t3", "/proj/a")

        events = event_log.read_events(
            filt=event_log.EventFilter(project_path="/proj/a")
        )
        assert {e["task_id"] for e in events} == {"t1", "t3"}

    def test_filter_by_task_id(self, fake_home):
        from xstitch import event_log

        event_log.append_event("task_created", "abc", "/proj")
        event_log.append_event("snapshot_added", "abc", "/proj")
        event_log.append_event("task_created", "xyz", "/proj")

        events = event_log.read_events(
            filt=event_log.EventFilter(task_id="abc")
        )
        assert len(events) == 2
        assert all(e["task_id"] == "abc" for e in events)

    def test_filter_by_agent(self, fake_home):
        from xstitch import event_log

        event_log.append_event("task_created", "t1", "/proj", agent="cursor")
        event_log.append_event("task_created", "t2", "/proj", agent="claude-code")

        events = event_log.read_events(
            filt=event_log.EventFilter(agent="cursor")
        )
        assert {e["task_id"] for e in events} == {"t1"}

    def test_filter_by_event_types(self, fake_home):
        from xstitch import event_log

        event_log.append_event("task_created", "t1", "/proj")
        event_log.append_event("snapshot_added", "t1", "/proj")
        event_log.append_event("decision_added", "t1", "/proj")

        events = event_log.read_events(
            filt=event_log.EventFilter(event_types=["snapshot_added", "decision_added"])
        )
        types = {e["event_type"] for e in events}
        assert types == {"snapshot_added", "decision_added"}


class TestResilience:

    def test_corrupted_line_is_skipped(self, fake_home):
        from xstitch import event_log

        event_log.append_event("task_created", "good1", "/proj")
        # Inject garbage mid-file.
        events_file = fake_home / "events.jsonl"
        with events_file.open("a") as f:
            f.write("this is not valid json at all\n")
        event_log.append_event("task_created", "good2", "/proj")

        events = event_log.read_events()
        ids = {e["task_id"] for e in events}
        assert ids == {"good1", "good2"}

    def test_missing_file_returns_empty(self, fake_home):
        from xstitch import event_log
        assert event_log.read_events() == []

    def test_unknown_event_type_is_written_anyway(self, fake_home, capsys):
        from xstitch import event_log
        ev = event_log.append_event("custom_thing", "t1", "/proj")
        assert ev is not None
        events = event_log.read_events()
        assert len(events) == 1
        assert events[0]["event_type"] == "custom_thing"
        # Verify warning was emitted.
        captured = capsys.readouterr()
        assert "Unknown event_type" in captured.err


class TestRotation:

    def test_rotation_on_size_threshold(self, fake_home, monkeypatch):
        # Drop the threshold to something tiny so one event triggers rotation.
        monkeypatch.setattr("xstitch.event_log.EVENT_LOG_ROTATE_BYTES", 50)
        from xstitch import event_log

        event_log.append_event(
            "task_created", "t1", "/proj", meta={"big": "x" * 200}
        )
        # After rotation, the active file is empty and an archive exists.
        active = fake_home / "events.jsonl"
        archives = list(fake_home.glob("events-*.jsonl"))
        assert archives, "expected a rotated archive file"
        assert not active.exists() or active.stat().st_size < 200

    def test_read_includes_archives_when_requested(self, fake_home, monkeypatch):
        monkeypatch.setattr("xstitch.event_log.EVENT_LOG_ROTATE_BYTES", 80)
        from xstitch import event_log

        event_log.append_event("task_created", "old1", "/proj", meta={"p": "x" * 100})
        event_log.append_event("task_created", "new1", "/proj")

        without = event_log.read_events(include_archives=False)
        with_archives = event_log.read_events(include_archives=True)
        assert len(with_archives) >= len(without)
        # old1 was rotated; it must be in the archive-inclusive read.
        assert "old1" in {e["task_id"] for e in with_archives}


# ---------------------------------------------------------------------------
# Concurrency: parallel writers must produce every event.
# ---------------------------------------------------------------------------


def _child_append_many(fake_home_str, n, tag):
    """Subprocess worker: append n events."""
    from pathlib import Path as P
    from unittest.mock import patch as _patch
    import xstitch.store as store_mod
    with _patch.object(store_mod, "GLOBAL_HOME", P(fake_home_str)), \
         _patch.object(store_mod, "PROJECTS_HOME", P(fake_home_str) / "projects"):
        from xstitch import event_log
        for i in range(n):
            event_log.append_event(
                event_type="task_created",
                task_id=f"{tag}-{i}",
                project_path="/proj",
                agent=tag,
            )


class TestConcurrentWriters:

    def test_parallel_appends_preserve_all_events(self, tmp_path):
        fake_home = tmp_path / "fake_ahcp_home"
        fake_home.mkdir()

        n_workers = 3
        n_per = 15
        ctx = multiprocessing.get_context("spawn")
        procs = []
        for i in range(n_workers):
            p = ctx.Process(
                target=_child_append_many,
                args=(str(fake_home), n_per, f"agent{i}"),
            )
            p.start()
            procs.append(p)
        for p in procs:
            p.join(timeout=30)
            assert p.exitcode == 0

        # Read back with patched home.
        with patch("xstitch.store.GLOBAL_HOME", fake_home), \
             patch("xstitch.store.PROJECTS_HOME", fake_home / "projects"):
            from xstitch import event_log
            events = event_log.read_events(limit=10_000, include_archives=True)

        assert len(events) == n_workers * n_per

        expected = {f"agent{i}-{j}" for i in range(n_workers) for j in range(n_per)}
        assert {e["task_id"] for e in events} == expected

        # All seqs are unique.
        seqs = [e["seq"] for e in events]
        assert len(set(seqs)) == len(seqs), "duplicate seq numbers detected"


# ---------------------------------------------------------------------------
# Cursor tracking
# ---------------------------------------------------------------------------


class TestCursors:

    def test_set_and_get_cursor(self, fake_home):
        from xstitch import event_log

        event_log.set_cursor("cursor")
        assert event_log.get_cursor("cursor") is not None
        assert "ts" in event_log.get_cursor("cursor")

    def test_cursors_are_per_agent(self, fake_home):
        from xstitch import event_log

        event_log.set_cursor("cursor", ts="2026-04-01T00:00:00+00:00")
        event_log.set_cursor("claude-code", ts="2026-04-02T00:00:00+00:00")

        assert event_log.get_cursor("cursor")["ts"] == "2026-04-01T00:00:00+00:00"
        assert event_log.get_cursor("claude-code")["ts"] == "2026-04-02T00:00:00+00:00"

    def test_unknown_cursor_returns_none(self, fake_home):
        from xstitch import event_log
        assert event_log.get_cursor("never-used") is None

    def test_cursor_filename_sanitized(self, fake_home):
        from xstitch import event_log
        event_log.set_cursor("weird/agent:name")
        # No path traversal; file exists somewhere under cursors/.
        cursors = list((fake_home / "cursors").iterdir())
        assert cursors
        # Retrieving with the same id still works.
        assert event_log.get_cursor("weird/agent:name") is not None
