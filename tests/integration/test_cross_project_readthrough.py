"""Tests for cross-project read-through and registry concurrency (Phase 2)."""

from __future__ import annotations

import json
import multiprocessing
import os
import sys
import time
from dataclasses import asdict
from pathlib import Path
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def dual_project_env(tmp_path, monkeypatch):
    """Set up two independent project scopes sharing a fake ~/.ahcp/ home.

    Returns ``(fake_global, home_project, repo_project)`` paths. Creates
    ``.git`` markers in both so the resolver keeps them separate.
    """
    fake_global = tmp_path / "fake_global"
    fake_projects = fake_global / "projects"
    fake_projects.mkdir(parents=True)

    home_project = tmp_path / "home"
    home_project.mkdir()
    (home_project / ".git").mkdir()

    repo_project = tmp_path / "repo"
    repo_project.mkdir()
    (repo_project / ".git").mkdir()

    # Patch GLOBAL_HOME and PROJECTS_HOME at the source (xstitch.store)
    # and ensure any module that imported them at load time is refreshed.
    with patch("xstitch.store.GLOBAL_HOME", fake_global), \
         patch("xstitch.store.PROJECTS_HOME", fake_projects):
        yield fake_global, home_project, repo_project


# ---------------------------------------------------------------------------
# Cross-project read-through (Store.get_task, get_snapshots, get_decisions)
# ---------------------------------------------------------------------------


class TestCrossProjectReadThrough:

    def test_get_task_falls_back_to_registry_when_not_local(self, dual_project_env):
        fake_global, home, repo = dual_project_env
        from xstitch.store import Store

        home_store = Store(str(home))
        task = home_store.create_task("stored in home", objective="see me from repo")

        # A fresh Store rooted at the repo should see it via registry fallback.
        repo_store = Store(str(repo))
        assert repo_store.project_key != home_store.project_key

        found = repo_store.get_task(task.id)
        assert found is not None
        assert found.id == task.id
        assert found.title == "stored in home"

    def test_get_snapshots_cross_project(self, dual_project_env):
        fake_global, home, repo = dual_project_env
        from xstitch.store import Store
        from xstitch.models import Snapshot

        home_store = Store(str(home))
        task = home_store.create_task("snap cross", objective="yay")
        home_store.add_snapshot(task.id, Snapshot(message="first snapshot from home agent"))
        home_store.add_snapshot(task.id, Snapshot(message="second snapshot from home agent"))

        repo_store = Store(str(repo))
        snaps = repo_store.get_snapshots(task.id, limit=10)
        assert len(snaps) == 2
        messages = [s.message for s in snaps]
        assert "first snapshot from home agent" in messages
        assert "second snapshot from home agent" in messages

    def test_get_decisions_cross_project(self, dual_project_env):
        fake_global, home, repo = dual_project_env
        from xstitch.store import Store
        from xstitch.models import Decision

        home_store = Store(str(home))
        task = home_store.create_task("decisions cross", objective="yay")
        home_store.add_decision(
            task.id,
            Decision(
                problem="which approach to take",
                chosen="option A",
                alternatives=["option B", "option C"],
                reasoning="A is faster",
            ),
        )

        repo_store = Store(str(repo))
        decs = repo_store.get_decisions(task.id)
        assert len(decs) == 1
        assert decs[0].problem == "which approach to take"
        assert decs[0].chosen == "option A"

    def test_for_task_returns_self_when_local(self, dual_project_env):
        fake_global, home, repo = dual_project_env
        from xstitch.store import Store

        home_store = Store(str(home))
        task = home_store.create_task("local task", objective="")
        assert home_store.for_task(task.id) is home_store

    def test_for_task_returns_owner_store_when_foreign(self, dual_project_env):
        fake_global, home, repo = dual_project_env
        from xstitch.store import Store

        home_store = Store(str(home))
        task = home_store.create_task("foreign task", objective="")

        repo_store = Store(str(repo))
        owner = repo_store.for_task(task.id)

        assert owner is not None
        assert owner.project_key == home_store.project_key
        assert owner.get_task(task.id).id == task.id

    def test_for_task_returns_none_for_unknown(self, dual_project_env):
        fake_global, home, repo = dual_project_env
        from xstitch.store import Store

        repo_store = Store(str(repo))
        assert repo_store.for_task("nope-bogus-id") is None

    def test_get_task_returns_none_for_unknown_everywhere(self, dual_project_env):
        fake_global, home, repo = dual_project_env
        from xstitch.store import Store

        Store(str(home)).create_task("something", objective="")

        repo_store = Store(str(repo))
        assert repo_store.get_task("does-not-exist") is None

    def test_build_handoff_writes_to_owning_project(self, dual_project_env):
        fake_global, home, repo = dual_project_env
        from xstitch.store import Store, PROJECTS_HOME, project_key
        from xstitch.models import Snapshot

        home_store = Store(str(home))
        task = home_store.create_task("handoff owner", objective="track me")
        home_store.add_snapshot(task.id, Snapshot(message="something significant happened here"))

        # Build from the REPO store; the handoff should land in the HOME
        # project's dir, not the repo's.
        repo_store = Store(str(repo))
        bundle = repo_store.build_handoff(task.id)
        assert bundle is not None

        home_handoff = (
            fake_global / "projects" / project_key(home.resolve())
            / "tasks" / task.id / "handoff.md"
        )
        repo_handoff = (
            fake_global / "projects" / project_key(repo.resolve())
            / "tasks" / task.id / "handoff.md"
        )
        assert home_handoff.exists()
        assert not repo_handoff.exists()

    def test_update_context_file_writes_to_owning_project(self, dual_project_env):
        fake_global, home, repo = dual_project_env
        from xstitch.store import Store, project_key
        from xstitch.models import Decision

        home_store = Store(str(home))
        task = home_store.create_task("ctx owner", objective="verify routing")
        home_store.add_decision(
            task.id,
            Decision(problem="big choice", chosen="the right one", reasoning="because"),
        )

        repo_store = Store(str(repo))
        repo_store.update_context_file(task.id)

        home_ctx = (
            fake_global / "projects" / project_key(home.resolve())
            / "tasks" / task.id / "context.md"
        )
        assert home_ctx.exists()
        assert "ctx owner" in home_ctx.read_text()


# ---------------------------------------------------------------------------
# BM25 indexing: cross-project tasks should have deep fields populated.
# ---------------------------------------------------------------------------


class TestBM25CrossProject:

    def test_bm25_reads_decisions_from_owning_project(self, dual_project_env):
        fake_global, home, repo = dual_project_env
        from xstitch.store import Store
        from xstitch.models import Decision
        from xstitch.relevance import BM25RelevanceEngine

        home_store = Store(str(home))
        task = home_store.create_task("foo task", objective="general thing")
        home_store.add_decision(
            task.id,
            Decision(
                problem="picking eucatur-specific library",
                chosen="use eucatur-sdk",
                reasoning="supports all the fields",
            ),
        )

        # Index from the REPO store — the task lives in HOME.
        repo_store = Store(str(repo))
        engine = BM25RelevanceEngine()
        engine.index(repo_store)

        # The task should be discoverable by a deep-field term ("eucatur").
        results = engine.search("eucatur")
        ids = [r["task"].id for r in results]
        assert task.id in ids


# ---------------------------------------------------------------------------
# Registry concurrency: parallel processes must not lose updates.
# ---------------------------------------------------------------------------


def _child_create_many(fake_global_str, project_str, n, tag):
    """Worker run in a subprocess: create ``n`` tasks with unique titles."""
    os.environ.pop("AHCP_PROJECT_PATH", None)
    # Re-import inside the child so patched module-level globals take effect
    # via the monkeypatched module (we manipulate GLOBAL_HOME via env).
    from pathlib import Path as P
    from unittest.mock import patch as _patch

    import xstitch.store as store_mod

    with _patch.object(store_mod, "GLOBAL_HOME", P(fake_global_str)), \
         _patch.object(store_mod, "PROJECTS_HOME", P(fake_global_str) / "projects"):
        from xstitch.store import Store

        s = Store(project_str)
        for i in range(n):
            s.create_task(title=f"{tag}-{i}", objective="race test")


class TestRegistryConcurrency:

    def test_parallel_writers_do_not_lose_registry_entries(self, tmp_path):
        fake_global = tmp_path / "fake_global"
        (fake_global / "projects").mkdir(parents=True)

        project = tmp_path / "repo"
        project.mkdir()
        (project / ".git").mkdir()

        n_per_worker = 20
        n_workers = 3
        ctx = multiprocessing.get_context("spawn")
        procs = []
        for i in range(n_workers):
            p = ctx.Process(
                target=_child_create_many,
                args=(str(fake_global), str(project), n_per_worker, f"w{i}"),
            )
            p.start()
            procs.append(p)

        for p in procs:
            p.join(timeout=30)
            assert p.exitcode == 0, f"worker {p.pid} failed with {p.exitcode}"

        registry = json.loads((fake_global / "registry.json").read_text())
        tasks = registry.get("tasks", [])
        assert len(tasks) == n_per_worker * n_workers, \
            f"expected {n_per_worker * n_workers} tasks, got {len(tasks)}"

        # Every (worker, index) pair should be represented exactly once.
        titles = {t["title"] for t in tasks}
        expected = {f"w{i}-{j}" for i in range(n_workers) for j in range(n_per_worker)}
        assert titles == expected


# ---------------------------------------------------------------------------
# Lock helper itself.
# ---------------------------------------------------------------------------


class TestFileLock:

    def test_lock_serializes_contention(self, tmp_path):
        from xstitch.locks import file_lock

        lock_path = tmp_path / "contested.lock"
        shared_counter_path = tmp_path / "counter.txt"
        shared_counter_path.write_text("0")

        def increment_under_lock():
            with file_lock(lock_path, timeout=10.0):
                val = int(shared_counter_path.read_text())
                time.sleep(0.01)  # widen the race window
                shared_counter_path.write_text(str(val + 1))

        import threading
        threads = [threading.Thread(target=increment_under_lock) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert int(shared_counter_path.read_text()) == 10

    def test_lock_times_out(self, tmp_path):
        from xstitch.locks import file_lock, FileLockTimeout

        lock_path = tmp_path / "blocked.lock"

        def hold_forever():
            with file_lock(lock_path, timeout=10.0):
                time.sleep(2.0)

        import threading
        t = threading.Thread(target=hold_forever)
        t.start()
        time.sleep(0.2)  # let the holder acquire first

        try:
            with pytest.raises(FileLockTimeout):
                with file_lock(lock_path, timeout=0.2):
                    pytest.fail("should not acquire while holder is active")
        finally:
            t.join()
