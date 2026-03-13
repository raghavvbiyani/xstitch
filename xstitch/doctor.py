"""Comprehensive diagnostic command for Stitch — like `brew doctor`.

Checks installation health, project setup, instruction file integrity,
enforcement hooks, and MCP server reachability. Provides actionable
fix instructions for every failure.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

from .healthcheck import diagnose as healthcheck_diagnose, check_editable_install
from .enforcement import check_claude_code_hooks
from .discovery import Stitch_SECTION_MARKER


PASS = "PASS"
FAIL = "FAIL"
WARN = "WARN"
SKIP = "SKIP"


def run_doctor(project_path: str | None = None, verbose: bool = False) -> list[dict]:
    """Run all diagnostic checks. Returns a list of check results."""
    project = Path(project_path or os.getcwd()).resolve()
    results = []

    # --- Installation checks ---
    for check in healthcheck_diagnose():
        status = _map_status(check.get("status", "ok"))
        results.append({
            "category": "Installation",
            "name": check["name"],
            "status": status,
            "detail": check.get("detail", check.get("reason", "")),
            "fix": check.get("fix", ""),
        })

    # --- Project checks ---
    from .store import PROJECTS_HOME, project_key as _project_key
    pkey = _project_key(project)
    proj_data_dir = PROJECTS_HOME / pkey
    old_stitch_dir = project / ".stitch"

    if proj_data_dir.exists() and (proj_data_dir / "tasks").exists():
        results.append({
            "category": "Project",
            "name": "Stitch initialized",
            "status": PASS,
            "detail": f"Data at {proj_data_dir}",
            "fix": "",
        })
    elif old_stitch_dir.exists() and (old_stitch_dir / "tasks").exists():
        results.append({
            "category": "Project",
            "name": "Stitch initialized",
            "status": WARN,
            "detail": f"Legacy .stitch/ in repo (will auto-migrate on next run)",
            "fix": "Run: python3 -m xstitch.cli auto-setup",
        })
    else:
        results.append({
            "category": "Project",
            "name": "Stitch initialized",
            "status": WARN,
            "detail": "No Stitch data found for this project",
            "fix": "Run: python3 -m xstitch.cli auto-setup",
        })

    active_task_file = proj_data_dir / "active_task"
    if active_task_file.exists():
        task_id = active_task_file.read_text().strip()
        meta = proj_data_dir / "tasks" / task_id / "meta.json"
        if meta.exists():
            results.append({
                "category": "Project",
                "name": "Active task",
                "status": PASS,
                "detail": f"Task {task_id}",
                "fix": "",
            })
        else:
            results.append({
                "category": "Project",
                "name": "Active task",
                "status": WARN,
                "detail": f"Active task {task_id} has no meta.json",
                "fix": "Run: python3 -m xstitch.cli task new \"title\"",
            })

    # --- Instruction file checks (only for installed tools) ---
    from .discovery import INJECTION_TARGETS, _get_installed_tool_names
    installed = _get_installed_tool_names()

    for target in INJECTION_TARGETS:
        name = target["file"]
        filepath = project / name

        if target["tool_key"] not in installed:
            continue

        if target["content"] == "mdc":
            if filepath.exists():
                content = filepath.read_text()
                has_stitch = "xstitch" in content.lower() and "alwaysApply: true" in content
                results.append({
                    "category": "Instructions",
                    "name": name,
                    "status": PASS if has_stitch else WARN,
                    "detail": "Stitch rule with alwaysApply: true" if has_stitch else "Missing or incomplete",
                    "fix": "" if has_stitch else "Run: python3 -m xstitch.cli inject",
                })
            else:
                results.append({
                    "category": "Instructions",
                    "name": name,
                    "status": WARN,
                    "detail": "File missing",
                    "fix": "Run: python3 -m xstitch.cli inject",
                })
        else:
            result = _check_instruction_file(name, filepath)
            results.append(result)

    # --- Enforcement checks (only for installed tools) ---
    if "Cursor" in installed:
        mdc_path = project / ".cursor" / "rules" / "stitch-context.mdc"
        if mdc_path.exists():
            content = mdc_path.read_text()
            if "alwaysApply: true" in content:
                results.append({
                    "category": "Enforcement",
                    "name": "Cursor alwaysApply",
                    "status": PASS,
                    "detail": "stitch-context.mdc has alwaysApply: true",
                    "fix": "",
                })
            else:
                results.append({
                    "category": "Enforcement",
                    "name": "Cursor alwaysApply",
                    "status": WARN,
                    "detail": "stitch-context.mdc missing alwaysApply: true",
                    "fix": "Run: python3 -m xstitch.cli inject",
                })

    if "Claude Code" in installed:
        hooks_result = check_claude_code_hooks()
        results.append({
            "category": "Enforcement",
            "name": "Claude Code hooks",
            "status": _map_status(hooks_result.get("status", "ok")),
            "detail": hooks_result.get("detail", hooks_result.get("reason", "")),
            "fix": hooks_result.get("fix", ""),
        })

    # --- Git hooks check ---
    git_hook = project / ".git" / "hooks" / "post-commit"
    if git_hook.exists() and "xstitch" in git_hook.read_text().lower():
        results.append({
            "category": "Enforcement",
            "name": "Git post-commit hook",
            "status": PASS,
            "detail": "Stitch post-commit hook installed",
            "fix": "",
        })
    elif (project / ".git").exists():
        results.append({
            "category": "Enforcement",
            "name": "Git post-commit hook",
            "status": WARN,
            "detail": "No Stitch git hook",
            "fix": "Run: python3 -m xstitch.cli hooks install",
        })

    # --- Global setup checks ---
    global_home = Path.home() / ".stitch"
    if global_home.exists():
        results.append({
            "category": "Global",
            "name": "Global home",
            "status": PASS,
            "detail": str(global_home),
            "fix": "",
        })
    else:
        results.append({
            "category": "Global",
            "name": "Global home",
            "status": WARN,
            "detail": "~/.stitch/ not found",
            "fix": "Run: python3 -m xstitch.cli global-setup",
        })

    return results


def format_doctor_report(results: list[dict]) -> str:
    """Format doctor results as a human-readable report."""
    lines = [
        "Stitch Doctor — System Health Check",
        "=" * 40,
        "",
    ]

    current_category = None
    pass_count = fail_count = warn_count = 0

    for r in results:
        if r["category"] != current_category:
            current_category = r["category"]
            lines.append(f"  {current_category}")
            lines.append(f"  {'-' * len(current_category)}")

        status = r["status"]
        if status == PASS:
            pass_count += 1
        elif status == FAIL:
            fail_count += 1
        elif status == WARN:
            warn_count += 1

        icon = {"PASS": "[PASS]", "FAIL": "[FAIL]", "WARN": "[WARN]", "SKIP": "[SKIP]"}
        detail = r.get("detail", "")
        lines.append(f"  {icon.get(status, '[????]')} {r['name']}: {detail}")

        if r.get("fix"):
            lines.append(f"         Fix: {r['fix']}")

    lines.append("")
    lines.append("-" * 40)
    lines.append(f"  {pass_count} passed, {warn_count} warnings, {fail_count} failures")

    if fail_count > 0:
        lines.append("")
        lines.append("  Fix failures above, then re-run: stitch doctor")

    return "\n".join(lines)


def _check_instruction_file(name: str, path: Path) -> dict:
    """Check an instruction file for proper Stitch injection."""
    if not path.exists():
        return {
            "category": "Instructions",
            "name": name,
            "status": WARN,
            "detail": "File missing",
            "fix": "Run: python3 -m xstitch.cli inject",
        }

    content = path.read_text()
    marker_count = content.count(Stitch_SECTION_MARKER)

    if marker_count == 2:
        return {
            "category": "Instructions",
            "name": name,
            "status": PASS,
            "detail": "Stitch section present and properly paired",
            "fix": "",
        }
    elif marker_count == 1:
        return {
            "category": "Instructions",
            "name": name,
            "status": WARN,
            "detail": "Corrupted: single marker (unpaired)",
            "fix": "Run: python3 -m xstitch.cli inject (will re-inject)",
        }
    else:
        return {
            "category": "Instructions",
            "name": name,
            "status": WARN,
            "detail": "No Stitch section found",
            "fix": "Run: python3 -m xstitch.cli inject",
        }


def _map_status(status: str) -> str:
    return {
        "ok": PASS,
        "broken": FAIL,
        "warning": WARN,
        "missing": WARN,
    }.get(status, WARN)
