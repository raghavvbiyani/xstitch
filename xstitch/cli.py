"""CLI for Stitch — Agent Handoff & Context Protocol.

Usage:
    stitch global-setup [--dry-run]          One-time: detect & configure all AI tools on this machine
    stitch auto-setup                        Per-project: idempotent bootstrap (init + inject + hooks)
    stitch auto "<prompt>"                   Intelligent routing: detect intent, find/create task
    stitch init                              Initialize Stitch in current project
    stitch task new "title" [-o "objective"] Create a new task
    stitch task list [--all]                 List tasks (--all for global)
    stitch task show [task-id]               Show task details
    stitch task switch <task-id>             Switch active task
    stitch task update --state/--next/--blockers  Update task fields
    stitch snap [-m "message"]               Take a snapshot
    stitch decide -p "problem" -c "chosen" [-a "alt1,alt2"] [-t "tradeoffs"] [-r "reason"]
    stitch handoff [task-id]                 Generate handoff bundle
    stitch resume [task-id]                  Generate structured resume briefing
    stitch smart-match "<query>"             BM25 relevance search across tasks
    stitch search <query>                    Search tasks by keyword
    stitch inject                            Inject into all agent config files (9 tools)
    stitch checkpoint -s "summary" [-d] [-e] [-f] [-q]  Rich pre-summarization checkpoint
    stitch doctor [--fix]                    Diagnose installation and config health
    stitch hooks install                     Install git hooks for auto-snapshots
    stitch daemon start [--interval 300]     Start background auto-snapshot daemon
    stitch daemon stop                       Stop the daemon
    stitch launchd install [--interval 600]  Auto-start daemon on login (survives reboot)
    stitch launchd uninstall                 Remove auto-start
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from .store import Store
from .models import Decision, HandoffBundle
from .capture import capture_snapshot, has_significant_changes


def main():
    parser = argparse.ArgumentParser(
        prog="xstitch",
        description="Agent Handoff & Context Protocol — preserve AI context across tools",
    )
    sub = parser.add_subparsers(dest="command")

    # --- init ---
    sub.add_parser("init", help="Initialize Stitch in current project")

    # --- task ---
    task_p = sub.add_parser("task", help="Task management")
    task_sub = task_p.add_subparsers(dest="task_command")

    new_p = task_sub.add_parser("new", help="Create a new task")
    new_p.add_argument("title", help="Task title")
    new_p.add_argument("-o", "--objective", default="", help="Task objective")
    new_p.add_argument("-t", "--tags", default="", help="Comma-separated tags")

    task_sub.add_parser("list", help="List tasks").add_argument(
        "--all", action="store_true", help="List tasks across all projects"
    )

    show_p = task_sub.add_parser("show", help="Show task details")
    show_p.add_argument("task_id", nargs="?", help="Task ID (default: active)")
    show_p.add_argument("--id", dest="flag_id", help="Task ID (alternative to positional)")

    switch_p = task_sub.add_parser("switch", help="Switch active task")
    switch_p.add_argument("task_id", nargs="?", help="Task ID to switch to")
    switch_p.add_argument("--id", dest="flag_id", help="Task ID (alternative to positional)")

    update_p = task_sub.add_parser("update", help="Update active task fields")
    update_p.add_argument("--state", help="Current state")
    update_p.add_argument("--next", help="Next steps")
    update_p.add_argument("--blockers", help="Blockers")
    update_p.add_argument("--status", help="Status (active/paused/completed/abandoned)")
    update_p.add_argument("task_id", nargs="?", help="Task ID (default: active)")
    update_p.add_argument("--id", dest="flag_id", help="Task ID (alternative to positional)")

    # --- snap ---
    snap_p = sub.add_parser("snap", help="Take a snapshot of current state")
    snap_p.add_argument("-m", "--message", default="", help="Snapshot message")
    snap_p.add_argument("--source", default="manual", help="Source (manual/agent)")
    snap_p.add_argument("task_id", nargs="?", help="Task ID (default: active)")
    snap_p.add_argument("--id", dest="flag_id", help="Task ID (alternative to positional)")

    # --- decide ---
    dec_p = sub.add_parser("decide", help="Log a technical decision")
    dec_p.add_argument("-p", "--problem", required=True, help="Problem being solved")
    dec_p.add_argument("-c", "--chosen", required=True, help="Chosen solution")
    dec_p.add_argument("-a", "--alternatives", default="", help="Alternatives (comma-sep)")
    dec_p.add_argument("-t", "--tradeoffs", default="", help="Tradeoffs")
    dec_p.add_argument("-r", "--reasoning", default="", help="Reasoning")
    dec_p.add_argument("task_id", nargs="?", help="Task ID (default: active)")
    dec_p.add_argument("--id", dest="flag_id", help="Task ID (alternative to positional)")

    # --- handoff ---
    ho_p = sub.add_parser("handoff", help="Generate handoff bundle")
    ho_p.add_argument("task_id", nargs="?", help="Task ID (default: active)")
    ho_p.add_argument("--id", dest="flag_id", help="Task ID (alternative to positional)")
    ho_p.add_argument("--budget", type=int, default=3000, help="Token budget")

    # --- resume ---
    res_p = sub.add_parser("resume", help="Generate resume prompt for new agent")
    res_p.add_argument("task_id", nargs="?", help="Task ID (default: active)")
    res_p.add_argument("--id", dest="flag_id", help="Task ID (alternative to positional)")

    # --- search ---
    search_p = sub.add_parser("search", help="Search tasks by keyword")
    search_p.add_argument("query", help="Search query")

    # --- global-setup ---
    gs_p = sub.add_parser("global-setup",
        help="One-time machine setup: detect AI tools, configure MCP + instructions globally")
    gs_p.add_argument("--dry-run", action="store_true",
        help="Show what would be configured without making changes")

    # --- auto-setup ---
    sub.add_parser("auto-setup", help="Idempotent project bootstrap (init + inject + hooks)")

    # --- smart-match ---
    sm_p = sub.add_parser("smart-match", help="Find tasks matching a query (keyword scoring)")
    sm_p.add_argument("query", help="Keywords or user prompt to match against tasks")

    # --- auto ---
    auto_p = sub.add_parser("auto", help="Intelligent routing: detect intent, find/create task, return context")
    auto_p.add_argument("prompt", help="User's prompt (the agent passes the user's message here)")

    # --- inject ---
    inject_p = sub.add_parser("inject", help="Inject agent discovery into agent config files")
    inject_p.add_argument("--all", action="store_true", dest="inject_all",
                          help="Inject into all files, even for tools not installed on this machine")

    # --- hooks ---
    hooks_p = sub.add_parser("hooks", help="Git hooks management")
    hooks_sub = hooks_p.add_subparsers(dest="hooks_command")
    hooks_sub.add_parser("install", help="Install git hooks")
    hooks_sub.add_parser("uninstall", help="Remove git hooks")

    # --- checkpoint (pre-summarization) ---
    cp_p = sub.add_parser("checkpoint", help="Rich snapshot before context summarization (saves reasoning, decisions, failures)")
    cp_p.add_argument("-s", "--summary", required=True, help="What has been accomplished so far")
    cp_p.add_argument("-d", "--decisions", default="", help="Key decisions made and why")
    cp_p.add_argument("-e", "--experiments", default="", help="What was tried (successful or not)")
    cp_p.add_argument("-f", "--failures", default="", help="What failed and why (dead ends)")
    cp_p.add_argument("-q", "--questions", default="", help="Open questions / unresolved issues")
    cp_p.add_argument("task_id", nargs="?", help="Task ID (default: active)")
    cp_p.add_argument("--id", dest="flag_id", help="Task ID (alternative to positional)")

    # --- daemon ---
    daemon_p = sub.add_parser("daemon", help="Background auto-snapshot daemon")
    daemon_sub = daemon_p.add_subparsers(dest="daemon_command")
    start_p = daemon_sub.add_parser("start", help="Start daemon")
    start_p.add_argument("--interval", type=int, default=300, help="Seconds between snapshots")
    daemon_sub.add_parser("stop", help="Stop daemon")
    daemon_sub.add_parser("status", help="Check daemon status")

    # --- doctor ---
    doc_p = sub.add_parser("doctor", help="Diagnose Stitch installation and configuration health")
    doc_p.add_argument("--fix", action="store_true", help="Attempt to auto-fix issues")
    doc_p.add_argument("-v", "--verbose", action="store_true", help="Show all checks including passing ones")

    # --- launchd (reboot-safe) ---
    ld_p = sub.add_parser("launchd", help="macOS LaunchAgent (survives reboot)")
    ld_sub = ld_p.add_subparsers(dest="launchd_command")
    ld_install = ld_sub.add_parser("install", help="Install LaunchAgent")
    ld_install.add_argument("--interval", type=int, default=600, help="Seconds between checks")
    ld_sub.add_parser("uninstall", help="Remove LaunchAgent")
    ld_sub.add_parser("status", help="Check LaunchAgent status")

    # --- cleanup (TTL-based stale task removal) ---
    cleanup_p = sub.add_parser("cleanup", help="Remove stale tasks older than N days")
    cleanup_p.add_argument("--days", type=int, default=45,
                           help="Remove tasks not updated in this many days (default: 45)")
    cleanup_p.add_argument("--dry-run", action="store_true",
                           help="Show what would be removed without deleting")

    # --- hook-handler (called by Claude Code hooks, reads stdin) ---
    hh_p = sub.add_parser("hook-handler", help="Handle Claude Code hook events (reads JSON from stdin)")
    hh_p.add_argument("--event", required=True, choices=["UserPromptSubmit", "Stop"],
                       help="The hook event name")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    store = Store()

    try:
        if args.command == "init":
            _cmd_init(store)
        elif args.command == "task":
            _cmd_task(store, args)
        elif args.command == "snap":
            _cmd_snap(store, args)
        elif args.command == "decide":
            _cmd_decide(store, args)
        elif args.command == "handoff":
            _cmd_handoff(store, args)
        elif args.command == "resume":
            _cmd_resume(store, args)
        elif args.command == "search":
            _cmd_search(store, args)
        elif args.command == "global-setup":
            _cmd_global_setup(args)
        elif args.command == "auto-setup":
            _cmd_auto_setup(store)
        elif args.command == "smart-match":
            _cmd_smart_match(store, args)
        elif args.command == "auto":
            _cmd_auto(store, args)
        elif args.command == "inject":
            _cmd_inject(store, args)
        elif args.command == "checkpoint":
            _cmd_checkpoint(store, args)
        elif args.command == "hooks":
            _cmd_hooks(store, args)
        elif args.command == "daemon":
            _cmd_daemon(store, args)
        elif args.command == "doctor":
            _cmd_doctor(store, args)
        elif args.command == "launchd":
            _cmd_launchd(args)
        elif args.command == "cleanup":
            _cmd_cleanup(args)
        elif args.command == "hook-handler":
            _cmd_hook_handler(store, args)
    except Exception as e:
        from . import log
        log.error(str(e))
        log.troubleshoot(
            f"Command 'stitch {args.command}' failed",
            "Run 'stitch doctor' to diagnose, or check: pip3 show xstitch && python3 -c 'import xstitch'",
        )
        sys.exit(1)


def _effective_task_id(args) -> str | None:
    """Resolve task_id from --id flag (preferred) or positional argument."""
    return getattr(args, "flag_id", None) or getattr(args, "task_id", None)


def _resolve_task_id(store: Store, explicit_id: str | None) -> str:
    if explicit_id:
        return explicit_id
    active = store.get_active_task_id()
    if not active:
        from . import log
        log.error("No active task found.")
        log.troubleshoot(
            "Stitch needs an active task to save snapshots, decisions, and checkpoints",
            "Run: python3 -m xstitch.cli task new \"your task title\"",
        )
        sys.exit(1)
    return active


# --- Command implementations ---

def _cmd_init(store: Store):
    path = store.init_project()
    print(f"Initialized Stitch at {path}")
    print("Next: stitch task new \"your task title\" -o \"objective\"")


def _cmd_task(store: Store, args):
    if args.task_command == "new":
        tags = [t.strip() for t in args.tags.split(",") if t.strip()] if args.tags else []
        task = store.create_task(title=args.title, objective=args.objective, tags=tags)
        print(f"Created task: {task.id} — {task.title}")
        print(f"Active task set to: {task.id}")

    elif args.task_command == "list":
        tasks = store.list_tasks(project_only=not getattr(args, "all", False))
        if not tasks:
            print("No tasks found. Create one with: stitch task new \"title\"")
            return
        active_id = store.get_active_task_id()
        for t in tasks:
            marker = " *" if t.id == active_id else ""
            print(f"  [{t.status:>10}] {t.id} — {t.title}{marker}")
        if active_id:
            print(f"\n  (* = active task)")

    elif args.task_command == "show":
        task_id = _resolve_task_id(store, _effective_task_id(args))
        task = store.get_task(task_id)
        if not task:
            print(f"Task {task_id} not found.")
            return
        print(f"Task: {task.title} ({task.id})")
        print(f"  Status:    {task.status}")
        print(f"  Project:   {task.project_path}")
        print(f"  Created:   {task.created_at}")
        print(f"  Updated:   {task.updated_at}")
        if task.tags:
            print(f"  Tags:      {', '.join(task.tags)}")
        if task.objective:
            print(f"  Objective: {task.objective}")
        if task.current_state:
            print(f"  State:     {task.current_state}")
        if task.next_steps:
            print(f"  Next:      {task.next_steps}")
        if task.blockers:
            print(f"  Blockers:  {task.blockers}")

        snaps = store.get_snapshots(task_id, limit=3)
        if snaps:
            print(f"\n  Recent snapshots ({len(snaps)}):")
            for s in snaps:
                print(f"    [{s.timestamp}] {s.message[:80]}")

        decs = store.get_decisions(task_id)
        if decs:
            print(f"\n  Decisions ({len(decs)}):")
            for d in decs:
                print(f"    - {d.problem[:60]} -> {d.chosen[:40]}")

    elif args.task_command == "switch":
        tid = _effective_task_id(args)
        if not tid:
            print("Error: task ID required. Usage: stitch task switch <task_id> or stitch task switch --id <task_id>")
            return
        if store.switch_task(tid):
            print(f"Switched to task: {tid}")
        else:
            print(f"Task {tid} not found.")

    elif args.task_command == "update":
        task_id = _resolve_task_id(store, _effective_task_id(args))
        task = store.get_task(task_id)
        if not task:
            print(f"Task {task_id} not found.")
            return
        if args.state:
            task.current_state = args.state
        if args.next:
            task.next_steps = args.next
        if args.blockers:
            task.blockers = args.blockers
        if args.status:
            task.status = args.status
        store.update_task(task)
        store.update_context_file(task_id)
        print(f"Updated task {task_id}.")

    else:
        print("Usage: stitch task {new|list|show|switch|update}")


def _cmd_snap(store: Store, args):
    from . import log
    task_id = _resolve_task_id(store, _effective_task_id(args))
    snap = capture_snapshot(
        message=args.message,
        source=args.source,
        cwd=str(store.project_path),
        task_id=task_id,
    )
    rejection = store.add_snapshot(task_id, snap)
    if rejection:
        log.skipped("Snapshot", rejection)
        return
    store.update_context_file(task_id)
    log.saved("Snapshot", f"[{snap.timestamp}] {snap.message[:80]}")


def _cmd_decide(store: Store, args):
    from . import log
    task_id = _resolve_task_id(store, _effective_task_id(args))
    alts = [a.strip() for a in args.alternatives.split(",") if a.strip()]
    decision = Decision(
        task_id=task_id,
        problem=args.problem,
        chosen=args.chosen,
        alternatives=alts,
        tradeoffs=args.tradeoffs,
        reasoning=args.reasoning,
    )
    rejection = store.add_decision(task_id, decision)
    if rejection:
        log.skipped("Decision", rejection)
        return
    store.update_context_file(task_id)
    log.saved("Decision", f"{decision.problem} -> {decision.chosen}")


def _cmd_handoff(store: Store, args):
    task_id = _resolve_task_id(store, _effective_task_id(args))
    bundle = store.build_handoff(task_id, token_budget=args.budget)
    if not bundle:
        print(f"Task {task_id} not found.")
        return
    print(bundle.to_markdown())
    print("\n---")
    print(f"Handoff bundle saved to: {store.tasks_dir / task_id / 'handoff.md'}")


def _cmd_resume(store: Store, args):
    from .relevance import generate_resume_briefing
    task_id = _resolve_task_id(store, _effective_task_id(args))
    task = store.get_task(task_id)
    if not task:
        print(f"Task {task_id} not found.")
        return
    print(generate_resume_briefing(task_id, store))


def _cmd_search(store: Store, args):
    results = store.search_tasks(args.query)
    if not results:
        print(f"No tasks found matching '{args.query}'.")
        return
    print(f"Found {len(results)} task(s):")
    for t in results:
        print(f"  [{t.status:>10}] {t.id} — {t.title}")
        if t.objective:
            print(f"               {t.objective[:80]}")


def _cmd_global_setup(args):
    from .global_setup import global_setup
    global_setup(dry_run=getattr(args, "dry_run", False))


def _cmd_auto_setup(store: Store):
    from .intelligence import auto_setup
    auto_setup(str(store.project_path))


def _cmd_smart_match(store: Store, args):
    from .intelligence import smart_match
    results = smart_match(args.query, store)
    if not results:
        print(f"No tasks matching '{args.query}'.")
        return
    for r in results:
        t = r["task"]
        conf = r["confidence"]
        evidence = r.get("evidence", [])
        field_scores = r.get("field_scores", {})
        print(f"  [{conf:.0%}] {t.id} — {t.title}")
        if evidence:
            print(f"        Evidence: {', '.join(evidence[:6])}")
        if field_scores:
            top_fields = sorted(field_scores.items(), key=lambda x: -x[1])[:3]
            print(f"        Top fields: {', '.join(f'{k}={v:.1f}' for k,v in top_fields)}")
        if t.objective:
            print(f"        Objective: {t.objective[:80]}")
        print()


def _cmd_auto(store: Store, args):
    from .intelligence import auto_route, format_auto_route_response
    result = auto_route(args.prompt, store)
    print(format_auto_route_response(result))


def _cmd_inject(store: Store, args=None):
    from .discovery import inject_agent_discovery
    force_all = getattr(args, "inject_all", False) if args else False
    inject_agent_discovery(str(store.project_path), force_all=force_all)


def _cmd_checkpoint(store: Store, args):
    from . import log
    from .capture import capture_pre_summarize_snapshot
    task_id = _resolve_task_id(store, _effective_task_id(args))
    snap = capture_pre_summarize_snapshot(
        summary=args.summary,
        decisions_made=args.decisions,
        experiments=args.experiments,
        failures=args.failures,
        open_questions=args.questions,
        cwd=str(store.project_path),
        task_id=task_id,
    )
    rejection = store.add_snapshot(task_id, snap)
    if rejection:
        log.skipped("Checkpoint", rejection)
        return
    store.update_context_file(task_id)
    store.build_handoff(task_id)
    log.saved("Checkpoint", f"PRE-SUMMARIZE: {args.summary[:60]}")
    log.ok("Context, handoff bundle, and decisions all updated on disk.")
    log.ok("This data survives context summarization and laptop restarts.")


def _cmd_hooks(store: Store, args):
    from .hooks import install_hooks, uninstall_hooks
    if args.hooks_command == "install":
        install_hooks(str(store.project_path))
    elif args.hooks_command == "uninstall":
        uninstall_hooks(str(store.project_path))
    else:
        print("Usage: stitch hooks {install|uninstall}")


def _cmd_daemon(store: Store, args):
    from .daemon import start_daemon, stop_daemon, daemon_status
    if args.daemon_command == "start":
        start_daemon(str(store.project_path), interval=args.interval)
    elif args.daemon_command == "stop":
        stop_daemon(str(store.project_path))
    elif args.daemon_command == "status":
        daemon_status(str(store.project_path))
    else:
        print("Usage: stitch daemon {start|stop|status}")


def _cmd_doctor(store: Store, args):
    from .doctor import run_doctor, format_doctor_report
    results = run_doctor(str(store.project_path), verbose=getattr(args, "verbose", False))
    print(format_doctor_report(results))

    if getattr(args, "fix", False):
        print()
        print("Attempting auto-fix...")
        from .intelligence import auto_setup
        auto_setup(str(store.project_path))
        from .enforcement import install_claude_code_hooks_global
        result = install_claude_code_hooks_global()
        print(f"  {result}")
        print("Re-run 'stitch doctor' to verify fixes.")


def _cmd_launchd(args):
    from .launchd import install_launchd, uninstall_launchd, launchd_status
    if args.launchd_command == "install":
        install_launchd(interval=args.interval)
    elif args.launchd_command == "uninstall":
        uninstall_launchd()
    elif args.launchd_command == "status":
        launchd_status()
    else:
        print("Usage: stitch launchd {install|uninstall|status}")


def _cmd_cleanup(args):
    """Manually run TTL cleanup to remove stale tasks."""
    from .store import PROJECTS_HOME, ACTIVE_TASK_FILE, _TTL_DAYS
    from datetime import datetime, timezone, timedelta

    days = args.days
    dry_run = args.dry_run
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    print(f"Stitch Cleanup — removing tasks not updated in {days}+ days")
    print(f"Cutoff: {cutoff.isoformat(timespec='seconds')}")
    if dry_run:
        print("DRY RUN — no files will be deleted")
    print()

    if not PROJECTS_HOME.exists():
        print("No projects found at ~/.stitch/projects/")
        return

    removed = 0
    skipped_active = 0

    for project_dir in sorted(PROJECTS_HOME.iterdir()):
        if not project_dir.is_dir():
            continue
        tasks_dir = project_dir / "tasks"
        if not tasks_dir.exists():
            continue

        active_id = ""
        af = project_dir / ACTIVE_TASK_FILE
        if af.exists():
            try:
                active_id = af.read_text().strip()
            except OSError:
                pass

        for task_dir in sorted(tasks_dir.iterdir()):
            if not task_dir.is_dir():
                continue
            task_id = task_dir.name
            meta_file = task_dir / "meta.json"
            if not meta_file.exists():
                continue

            try:
                meta = json.loads(meta_file.read_text())
            except (json.JSONDecodeError, OSError):
                continue

            title = meta.get("title", "(untitled)")
            status = meta.get("status", "unknown")
            updated = meta.get("updated_at", "")

            if task_id == active_id:
                skipped_active += 1
                if dry_run:
                    print(f"  SKIP (active task): {task_id[:8]} — {title}")
                continue

            if status == "active":
                skipped_active += 1
                if dry_run:
                    print(f"  SKIP (status=active): {task_id[:8]} — {title}")
                continue

            try:
                updated_dt = datetime.fromisoformat(updated)
                if updated_dt >= cutoff:
                    continue
            except (ValueError, TypeError):
                try:
                    mtime = meta_file.stat().st_mtime
                    if datetime.fromtimestamp(mtime, tz=timezone.utc) >= cutoff:
                        continue
                except OSError:
                    continue

            if dry_run:
                print(f"  WOULD REMOVE: {task_id[:8]} — {title} (updated: {updated}, status: {status})")
            else:
                try:
                    import shutil
                    shutil.rmtree(task_dir)
                    print(f"  Removed: {task_id[:8]} — {title} (updated: {updated})")
                    removed += 1
                except OSError as e:
                    print(f"  ERROR removing {task_id[:8]}: {e}")

    print()
    if dry_run:
        print("Dry run complete. Use without --dry-run to actually delete.")
    else:
        print(f"Removed {removed} task(s). Skipped {skipped_active} active task(s).")
        if removed:
            store = Store()
            store._prune_registry_stale_entries()
            print("Global registry pruned.")


def _cmd_hook_handler(store: Store, args):
    """Handle Claude Code hook events.

    Reads JSON from stdin (provided by Claude Code), performs the appropriate
    action, and outputs structured JSON to stdout.

    For UserPromptSubmit, outputs JSON with:
    - systemMessage: visible warning shown to the user in the UI
    - additionalContext: injected into the agent's conversation context
    """
    from . import log
    from .intelligence import auto_setup, auto_route, format_auto_route_response

    event = args.event
    stdin_data = {}
    try:
        raw = sys.stdin.read()
        if raw.strip():
            stdin_data = json.loads(raw)
    except (json.JSONDecodeError, OSError):
        pass

    if event == "UserPromptSubmit":
        auto_setup(str(store.project_path), quiet=True)

        prompt = stdin_data.get("prompt", "")
        if not prompt:
            return

        result = auto_route(prompt, store)
        response = format_auto_route_response(result)

        system_msg, context_msg = _build_hook_messages(result, response)

        hook_output = {}
        if system_msg:
            hook_output["systemMessage"] = system_msg
        if context_msg:
            hook_output["hookSpecificOutput"] = {
                "hookEventName": "UserPromptSubmit",
                "additionalContext": context_msg,
            }

        if hook_output:
            print(json.dumps(hook_output))

    elif event == "Stop":
        active = store.get_active_task_id()
        if active:
            from .capture import capture_snapshot
            snap = capture_snapshot(
                message="Agent session ended",
                source="hook",
                cwd=str(store.project_path),
                task_id=active,
            )
            rejection = store.add_snapshot(active, snap)
            if not rejection:
                store.update_context_file(active)
                log.saved("Snapshot", "Agent session ended (Stop hook)")


def _build_hook_messages(result: dict, full_response: str) -> tuple[str, str]:
    """Build systemMessage (shown to user) and additionalContext (injected for agent)."""
    action = result.get("action")
    task = result.get("task")

    if action == "resumed" and task:
        conf = result.get("confidence", 0)
        return (
            f"[Stitch] Resumed task: '{task.title}' ({conf:.0%} match). Context loaded from previous session.",
            f"Stitch CONTEXT (you MUST follow this):\n"
            f"Active task: {task.id} — {task.title}\n"
            f"You MUST push snapshots (stitch_snapshot) after completing sub-tasks and decisions (stitch_add_decision) for architectural choices.\n"
            f"Push every 2-3 minutes of active work.\n\n"
            f"{full_response}",
        )
    elif action == "created" and task:
        return (
            f"[Stitch] Created new task: '{task.title}' (ID: {task.id[:8]}). Context will be saved.",
            f"Stitch CONTEXT (you MUST follow this):\n"
            f"New task created: {task.id} — {task.title}\n"
            f"You MUST push snapshots (stitch_snapshot) after completing sub-tasks and decisions (stitch_add_decision) for architectural choices.\n"
            f"Push every 2-3 minutes of active work.\n\n"
            f"{full_response}",
        )
    elif action == "loaded_active" and task:
        return (
            f"[Stitch] Active task: '{task.title}'. Context loaded from previous session.",
            f"Stitch CONTEXT (you MUST follow this):\n"
            f"Active task: {task.id} — {task.title}\n"
            f"You MUST push snapshots (stitch_snapshot) after completing sub-tasks and decisions (stitch_add_decision) for architectural choices.\n"
            f"Push every 2-3 minutes of active work.\n\n"
            f"{full_response}",
        )
    elif action == "found_in_other_project" and task:
        return (
            f"[Stitch] Found related task '{task.title}' in another project. Creating new task here.",
            f"Stitch CONTEXT: Related work exists in another project but this is a different workspace.\n"
            f"Create a new task for this work if appropriate.\n"
            f"Push snapshots and decisions per the Stitch protocol.",
        )
    elif action == "active_task_exists" and task:
        return (
            f"[Stitch] Active task: '{task.title}' (not loaded — prompt doesn't appear related).",
            f"Stitch CONTEXT: There is an active task '{task.title}' ({task.id}) but the user's prompt "
            f"doesn't appear related to it. Do NOT load or resume it unless the user explicitly asks. "
            f"If the user wants to resume, they can say 'resume' or 'continue the task'.",
        )
    elif action == "greeting":
        return ("", "")
    else:
        return (
            "[Stitch] No prior context found. Starting fresh.",
            "Stitch CONTEXT: No matching task found. Create a new task with stitch_snapshot if this is meaningful work.\n"
            "Push snapshots and decisions per the Stitch protocol.",
        )


if __name__ == "__main__":
    main()
