#!/usr/bin/env python3
"""Stitch Cross-Agent E2E Verification Script.

Validates that Stitch context is correctly preserved and propagated across
different AI agent sessions. Run after each phase to check that prior
context is intact and new context was added correctly.

Usage:
    python3 e2e_verify.py --phase 0 --project /tmp/stitch-e2e-crossagent
    python3 e2e_verify.py --phase 1 --project /tmp/stitch-e2e-crossagent
    python3 e2e_verify.py --phase 2 --project /tmp/stitch-e2e-crossagent
    python3 e2e_verify.py --phase 3 --project /tmp/stitch-e2e-crossagent
    python3 e2e_verify.py --phase 4 --project /tmp/stitch-e2e-crossagent
    python3 e2e_verify.py --phase all --project /tmp/stitch-e2e-crossagent
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# Colours
# ---------------------------------------------------------------------------

GREEN = "\033[0;32m"
RED = "\033[0;31m"
YELLOW = "\033[1;33m"
CYAN = "\033[0;36m"
BOLD = "\033[1m"
NC = "\033[0m"

# ---------------------------------------------------------------------------
# Helpers — read Stitch data from disk
# ---------------------------------------------------------------------------

class StitchInspector:
    """Reads .stitch/ data files for a project and provides query helpers."""

    def __init__(self, project_path: str):
        self.project = Path(project_path)
        self.stitch_dir = self.project / ".stitch"
        self.tasks_dir = self.stitch_dir / "tasks"
        self._task_id: str | None = None
        self._meta: dict | None = None
        self._decisions: list[dict] | None = None
        self._snapshots: list[dict] | None = None

    @property
    def task_id(self) -> str | None:
        if self._task_id is None:
            active_file = self.stitch_dir / "active_task"
            if active_file.exists():
                self._task_id = active_file.read_text().strip()
        return self._task_id

    @property
    def task_dir(self) -> Path | None:
        if self.task_id:
            return self.tasks_dir / self.task_id
        return None

    @property
    def meta(self) -> dict:
        if self._meta is None:
            self._meta = self._load_json("meta.json") or {}
        return self._meta

    @property
    def decisions(self) -> list[dict]:
        if self._decisions is None:
            self._decisions = self._load_json("decisions.json") or []
        return self._decisions

    @property
    def snapshots(self) -> list[dict]:
        if self._snapshots is None:
            self._snapshots = self._load_json("snapshots.json") or []
        return self._snapshots

    def _load_json(self, filename: str) -> list | dict | None:
        if not self.task_dir:
            return None
        f = self.task_dir / filename
        if not f.exists():
            return None
        try:
            return json.loads(f.read_text())
        except (json.JSONDecodeError, OSError):
            return None

    # --- Query helpers ---

    def task_exists(self) -> bool:
        return bool(self.task_id and self.task_dir and self.task_dir.exists())

    def task_title(self) -> str:
        return self.meta.get("title", "")

    def task_status(self) -> str:
        return self.meta.get("status", "")

    def task_objective(self) -> str:
        return self.meta.get("objective", "")

    def task_current_state(self) -> str:
        return self.meta.get("current_state", "")

    def task_next_steps(self) -> str:
        return self.meta.get("next_steps", "")

    def decision_count(self) -> int:
        return len(self.decisions)

    def snapshot_count(self) -> int:
        return len(self.snapshots)

    def has_decision_containing(self, *keywords: str) -> tuple[bool, str]:
        """Check if any decision contains ALL of the given keywords (case-insensitive).

        Returns (matched, evidence_string).
        """
        kw_lower = [k.lower() for k in keywords]
        for d in self.decisions:
            blob = json.dumps(d).lower()
            if all(k in blob for k in kw_lower):
                problem = d.get("problem", "?")
                chosen = d.get("chosen", "?")
                return True, f"{problem} -> {chosen}"
        return False, f"no decision containing all of: {keywords}"

    def has_decision_containing_any(self, *keywords: str) -> tuple[bool, str]:
        """Check if any decision contains ANY of the given keywords."""
        kw_lower = [k.lower() for k in keywords]
        for d in self.decisions:
            blob = json.dumps(d).lower()
            for k in kw_lower:
                if k in blob:
                    problem = d.get("problem", "?")
                    chosen = d.get("chosen", "?")
                    return True, f"{problem} -> {chosen} (matched: '{k}')"
        return False, f"no decision containing any of: {keywords}"

    def has_snapshot_containing(self, *keywords: str) -> tuple[bool, str]:
        """Check if any snapshot message contains ALL keywords."""
        kw_lower = [k.lower() for k in keywords]
        for s in self.snapshots:
            msg = s.get("message", "").lower()
            blob = json.dumps(s).lower()
            text = msg + " " + blob
            if all(k in text for k in kw_lower):
                return True, s.get("message", "?")[:120]
        return False, f"no snapshot containing all of: {keywords}"

    def has_snapshot_containing_any(self, *keywords: str) -> tuple[bool, str]:
        """Check if any snapshot message contains ANY keyword."""
        kw_lower = [k.lower() for k in keywords]
        for s in self.snapshots:
            blob = json.dumps(s).lower()
            for k in kw_lower:
                if k in blob:
                    return True, s.get("message", "?")[:120]
        return False, f"no snapshot containing any of: {keywords}"

    def context_contains(self, *keywords: str) -> tuple[bool, str]:
        """Search across ALL task data (meta, decisions, snapshots) for keywords."""
        kw_lower = [k.lower() for k in keywords]
        all_text = json.dumps(self.meta).lower()
        all_text += " " + json.dumps(self.decisions).lower()
        all_text += " " + json.dumps(self.snapshots).lower()
        context_md = self.task_dir / "context.md" if self.task_dir else None
        if context_md and context_md.exists():
            all_text += " " + context_md.read_text().lower()
        for k in kw_lower:
            if k in all_text:
                return True, f"found '{k}' in task context"
        return False, f"none of {keywords} found in any task data"

    def resume_briefing_contains(self, *keywords: str) -> tuple[bool, str]:
        """Run `stitch resume` and check if the output contains keywords."""
        try:
            result = subprocess.run(
                [sys.executable, "-m", "xstitch.cli", "resume"],
                capture_output=True, text=True, timeout=10,
                cwd=str(self.project),
            )
            output = result.stdout.lower()
            kw_lower = [k.lower() for k in keywords]
            for k in kw_lower:
                if k in output:
                    return True, f"resume briefing contains '{k}'"
            return False, f"resume briefing missing: {keywords}"
        except Exception as e:
            return False, f"resume command failed: {e}"

    def file_exists(self, filename: str) -> bool:
        return (self.project / filename).exists()

    def file_size(self, filename: str) -> int:
        f = self.project / filename
        return f.stat().st_size if f.exists() else 0

    def file_contains(self, filename: str, *keywords: str) -> tuple[bool, str]:
        """Check if a file in the project contains any of the keywords."""
        f = self.project / filename
        if not f.exists():
            return False, f"file {filename} does not exist"
        try:
            content = f.read_text().lower()
        except Exception:
            return False, f"could not read {filename}"
        kw_lower = [k.lower() for k in keywords]
        for k in kw_lower:
            if k in content:
                return True, f"{filename} contains '{k}'"
        return False, f"{filename} missing all of: {keywords}"


# ---------------------------------------------------------------------------
# Check runner
# ---------------------------------------------------------------------------

class CheckResult:
    def __init__(self, name: str, passed: bool, detail: str = ""):
        self.name = name
        self.passed = passed
        self.detail = detail

    def __str__(self):
        status = f"{GREEN}PASS{NC}" if self.passed else f"{RED}FAIL{NC}"
        detail = f" — {self.detail}" if self.detail else ""
        return f"  [{status}] {self.name}{detail}"


def run_checks(phase: int, checks: list[CheckResult]) -> bool:
    print(f"\n{BOLD}{'=' * 56}{NC}")
    print(f"{BOLD}  Stitch E2E Verification — Phase {phase}{NC}")
    print(f"{BOLD}{'=' * 56}{NC}\n")

    passed = sum(1 for c in checks if c.passed)
    total = len(checks)

    for c in checks:
        print(str(c))

    print()
    if passed == total:
        print(f"  {GREEN}{BOLD}Result: {passed}/{total} PASSED{NC}")
    else:
        failed = total - passed
        print(f"  {RED}{BOLD}Result: {failed}/{total} FAILED{NC}  ({passed} passed)")

    print()
    return passed == total


# ---------------------------------------------------------------------------
# Phase-specific checks
# ---------------------------------------------------------------------------

def phase_0_checks(ins: StitchInspector) -> list[CheckResult]:
    """Baseline: verify the setup script seeded data correctly."""
    checks = []

    # 1. .stitch directory exists
    checks.append(CheckResult(
        "Stitch directory exists",
        ins.stitch_dir.exists(),
        str(ins.stitch_dir) if ins.stitch_dir.exists() else "missing .stitch/",
    ))

    # 2. Task exists
    checks.append(CheckResult(
        "Task exists",
        ins.task_exists(),
        f"id: {ins.task_id}" if ins.task_exists() else "no active task",
    ))

    # 3. Task title is correct
    title_ok = "rate limiter" in ins.task_title().lower()
    checks.append(CheckResult(
        "Task title mentions rate limiter",
        title_ok,
        ins.task_title()[:80],
    ))

    # 4. Dead-end decision seeded
    dec_ok, dec_detail = ins.has_decision_containing_any("sliding window", "abandoned")
    checks.append(CheckResult(
        "Dead-end decision seeded (sliding window)",
        dec_ok,
        dec_detail[:120],
    ))

    # 5. Dead-end snapshot seeded
    snap_ok, snap_detail = ins.has_snapshot_containing_any("failed", "sliding window", "abandoned")
    checks.append(CheckResult(
        "Dead-end snapshot seeded",
        snap_ok,
        snap_detail[:120],
    ))

    # 6. At least 2 snapshots (failed approach + exploration)
    scount = ins.snapshot_count()
    checks.append(CheckResult(
        "At least 2 snapshots seeded",
        scount >= 2,
        f"found: {scount}",
    ))

    # 7. Skeleton rate_limiter.py exists
    checks.append(CheckResult(
        "Skeleton rate_limiter.py exists",
        ins.file_exists("rate_limiter.py"),
        "rate_limiter.py" if ins.file_exists("rate_limiter.py") else "missing",
    ))

    # 8. Task has current_state set
    state_ok = len(ins.task_current_state()) > 10
    checks.append(CheckResult(
        "Task current_state is set",
        state_ok,
        ins.task_current_state()[:100] if state_ok else "(empty or too short)",
    ))

    # 9. Task has next_steps set
    next_ok = len(ins.task_next_steps()) > 10
    checks.append(CheckResult(
        "Task next_steps is set",
        next_ok,
        ins.task_next_steps()[:100] if next_ok else "(empty or too short)",
    ))

    return checks


def phase_1_checks(ins: StitchInspector) -> list[CheckResult]:
    """After Phase 1 (Cursor): algorithm chosen, implementation started."""
    checks = phase_0_checks(ins)

    # Phase 1 specific: a NEW algorithm decision (not the seeded dead-end)
    algo_ok, algo_detail = ins.has_decision_containing_any(
        "token bucket", "fixed window", "leaky bucket", "algorithm",
    )
    checks.append(CheckResult(
        "Algorithm decision recorded (post Phase 1)",
        algo_ok,
        algo_detail[:120],
    ))

    # At least 2 decisions now (seeded dead-end + new algorithm choice)
    checks.append(CheckResult(
        "At least 2 decisions total",
        ins.decision_count() >= 2,
        f"found: {ins.decision_count()}",
    ))

    # Implementation file has a concrete limiter class
    impl_ok, impl_detail = ins.file_contains(
        "rate_limiter.py",
        "tokenbucket", "token_bucket", "fixedwindow", "fixed_window",
        "leakybucket", "leaky_bucket", "def allow",
    )
    checks.append(CheckResult(
        "rate_limiter.py has a concrete algorithm implementation",
        impl_ok,
        impl_detail[:120],
    ))

    # More snapshots than baseline
    scount = ins.snapshot_count()
    checks.append(CheckResult(
        "At least 3 snapshots (baseline + Phase 1 work)",
        scount >= 3,
        f"found: {scount}",
    ))

    # Dead-end from Phase 0 is STILL preserved
    dead_ok, dead_detail = ins.has_decision_containing_any("sliding window", "abandoned")
    checks.append(CheckResult(
        "Phase 0 dead-end decision still preserved",
        dead_ok,
        dead_detail[:120],
    ))

    return checks


def phase_2_checks(ins: StitchInspector) -> list[CheckResult]:
    """After Phase 2 (Claude Code): storage backend added."""
    checks = phase_1_checks(ins)

    # Storage/backend decision recorded
    storage_ok, storage_detail = ins.has_decision_containing_any(
        "redis", "storage", "backend", "file-based", "sqlite",
        "memory", "persistence", "multi-process",
    )
    checks.append(CheckResult(
        "Storage/backend decision recorded (Phase 2)",
        storage_ok,
        storage_detail[:120],
    ))

    # At least 3 decisions now
    checks.append(CheckResult(
        "At least 3 decisions total",
        ins.decision_count() >= 3,
        f"found: {ins.decision_count()}",
    ))

    # Resume briefing contains warning about sliding window dead-end
    warn_ok, warn_detail = ins.resume_briefing_contains("sliding window")
    checks.append(CheckResult(
        "Resume briefing warns about sliding window dead-end",
        warn_ok,
        warn_detail[:120],
    ))

    # In-memory or fallback is mentioned somewhere in context
    mem_ok, mem_detail = ins.context_contains(
        "in-memory", "in_memory", "memory", "fallback", "mock",
    )
    checks.append(CheckResult(
        "In-memory/fallback mode noted in context",
        mem_ok,
        mem_detail[:120],
    ))

    # More snapshots
    scount = ins.snapshot_count()
    checks.append(CheckResult(
        "At least 5 snapshots (baseline + Phase 1 + Phase 2)",
        scount >= 5,
        f"found: {scount}",
    ))

    # Implementation has backend-related code
    backend_ok, backend_detail = ins.file_contains(
        "rate_limiter.py",
        "backend", "storage", "redis", "store", "persist",
    )
    checks.append(CheckResult(
        "rate_limiter.py has storage/backend code",
        backend_ok,
        backend_detail[:120],
    ))

    return checks


def phase_3_checks(ins: StitchInspector) -> list[CheckResult]:
    """After Phase 3 (Codex): tests written, bugs documented."""
    checks = phase_2_checks(ins)

    # Test file exists
    test_exists = (
        ins.file_exists("test_rate_limiter.py")
        or ins.file_exists("tests/test_rate_limiter.py")
        or ins.file_exists("tests.py")
        or ins.file_exists("test_limiter.py")
    )
    test_name = "test_rate_limiter.py"
    for candidate in ["test_rate_limiter.py", "tests/test_rate_limiter.py",
                       "tests.py", "test_limiter.py"]:
        if ins.file_exists(candidate):
            test_name = candidate
            break
    checks.append(CheckResult(
        "Test file exists",
        test_exists,
        test_name if test_exists else "no test file found",
    ))

    # All Phase 1 decisions still intact
    checks.append(CheckResult(
        "All prior decisions still intact (>= 3)",
        ins.decision_count() >= 3,
        f"found: {ins.decision_count()}",
    ))

    # More snapshots
    scount = ins.snapshot_count()
    checks.append(CheckResult(
        "At least 7 snapshots (through Phase 3)",
        scount >= 7,
        f"found: {scount}",
    ))

    # Snapshot or decision about testing/bugs
    test_snap_ok, test_snap_detail = ins.has_snapshot_containing_any(
        "test", "bug", "race condition", "concurrency", "edge case",
    )
    if not test_snap_ok:
        test_snap_ok, test_snap_detail = ins.context_contains(
            "test", "bug", "race", "concurrency",
        )
    checks.append(CheckResult(
        "Testing/bug activity recorded in snapshots or context",
        test_snap_ok,
        test_snap_detail[:120],
    ))

    return checks


def phase_4_checks(ins: StitchInspector) -> list[CheckResult]:
    """After Phase 4 (Antigravity): fix bugs, docs, mark complete."""
    checks = phase_3_checks(ins)

    # At least 3 decisions across all phases
    checks.append(CheckResult(
        "3+ decisions across all phases",
        ins.decision_count() >= 3,
        f"found: {ins.decision_count()}",
    ))

    # Task status updated (completed, or state reflects completion)
    status = ins.task_status()
    state = ins.task_current_state().lower()
    status_ok = status == "completed" or "complete" in state or "done" in state or "finish" in state
    checks.append(CheckResult(
        "Task marked completed or state reflects completion",
        status_ok,
        f"status='{status}', state snippet='{state[:60]}'",
    ))

    # Documentation updated — README has more than the skeleton
    readme_size = ins.file_size("README.md")
    checks.append(CheckResult(
        "README.md substantially updated (> 500 bytes)",
        readme_size > 500,
        f"size: {readme_size} bytes",
    ))

    # All original dead-ends still preserved
    dead_ok, dead_detail = ins.context_contains("sliding window")
    checks.append(CheckResult(
        "Original sliding window dead-end still in context",
        dead_ok,
        dead_detail[:120],
    ))

    # More snapshots
    scount = ins.snapshot_count()
    checks.append(CheckResult(
        "At least 9 snapshots total (all phases)",
        scount >= 9,
        f"found: {scount}",
    ))

    return checks


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

PHASE_RUNNERS = {
    0: phase_0_checks,
    1: phase_1_checks,
    2: phase_2_checks,
    3: phase_3_checks,
    4: phase_4_checks,
}


def main():
    parser = argparse.ArgumentParser(
        description="Stitch Cross-Agent E2E Verification",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 e2e_verify.py --phase 0 --project /tmp/stitch-e2e-crossagent
  python3 e2e_verify.py --phase 1
  python3 e2e_verify.py --phase all
        """,
    )
    parser.add_argument(
        "--phase", required=True,
        help="Phase to verify (0-4) or 'all' for cumulative check",
    )
    parser.add_argument(
        "--project", default="/tmp/stitch-e2e-crossagent",
        help="Path to the test project (default: /tmp/stitch-e2e-crossagent)",
    )
    args = parser.parse_args()

    project = Path(args.project)
    if not project.exists():
        print(f"{RED}Error: project directory {project} does not exist.{NC}")
        print(f"Run e2e_setup.sh first to create the test project.")
        sys.exit(1)

    ins = StitchInspector(str(project))

    if args.phase == "all":
        all_passed = True
        for phase in range(5):
            checks = PHASE_RUNNERS[phase](ins)
            if not run_checks(phase, checks):
                all_passed = False
        sys.exit(0 if all_passed else 1)
    else:
        try:
            phase = int(args.phase)
        except ValueError:
            print(f"{RED}Error: --phase must be 0-4 or 'all'{NC}")
            sys.exit(1)

        if phase not in PHASE_RUNNERS:
            print(f"{RED}Error: --phase must be 0-4 or 'all'{NC}")
            sys.exit(1)

        checks = PHASE_RUNNERS[phase](ins)
        ok = run_checks(phase, checks)
        sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
