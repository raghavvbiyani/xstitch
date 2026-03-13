"""MCP Server for Stitch — exposes context operations as MCP tools.

This server can be registered in any MCP-compatible AI tool (Cursor, Claude Code,
Codex, Windsurf, Gemini CLI, Copilot CLI, Continue.dev, Zed, etc.) so agents can
natively read/write task context without CLI.

Run as:
    python -u -m xstitch.mcp_server                          # stdio mode
    python -u -m xstitch.mcp_server --project /path/to/proj  # specific project

The -u flag forces unbuffered stdout, critical for MCP stdio transport. Without it
Python block-buffers stdout when piped, causing the client to never receive responses.

Register in any tool's MCP config (see README for per-tool examples).
"""

from __future__ import annotations

import json
import sys


# Force unbuffered binary I/O on stdin/stdout.
_stdin = sys.stdin.buffer if hasattr(sys.stdin, "buffer") else sys.stdin
_stdout = sys.stdout.buffer if hasattr(sys.stdout, "buffer") else sys.stdout

# Transport protocol auto-detection.
#
# MCP has two stdio framing conventions:
#   1. Content-Length framing (LSP-style, used by Cursor, Claude Code, Windsurf)
#      Header:  Content-Length: 123\r\n\r\n{"jsonrpc":"2.0",...}
#   2. NDJSON (newline-delimited JSON, MCP spec 2025-06-18, used by Codex/rmcp)
#      Each message is a single JSON line:  {"jsonrpc":"2.0",...}\n
#
# We auto-detect from the first byte of the first message:
#   '{' → NDJSON,  'C' → Content-Length.  Then use that mode for all I/O.
_transport: str = ""  # "ndjson" or "content-length", set on first _read()


def _send(msg: dict):
    """Send a JSON-RPC message in the detected transport format."""
    payload = json.dumps(msg).encode("utf-8")
    if _transport == "ndjson":
        _stdout.write(payload + b"\n")
    else:
        header = f"Content-Length: {len(payload)}\r\n\r\n".encode("ascii")
        _stdout.write(header)
        _stdout.write(payload)
    _stdout.flush()


def _read_ndjson() -> dict | None:
    """Read a single NDJSON line from stdin."""
    while True:
        raw_line = _stdin.readline()
        if not raw_line:
            return None
        line = raw_line.decode("utf-8", errors="replace").strip()
        if not line:
            continue
        return json.loads(line)


def _read_content_length(first_line: str) -> dict | None:
    """Read a Content-Length framed message. first_line is the already-read header."""
    content_length = -1
    if first_line.startswith("Content-Length:"):
        content_length = int(first_line.split(":", 1)[1].strip())

    while True:
        raw_line = _stdin.readline()
        if not raw_line:
            return None
        line = raw_line.decode("utf-8", errors="replace").strip()
        if line.startswith("Content-Length:"):
            content_length = int(line.split(":", 1)[1].strip())
        elif line == "":
            if content_length >= 0:
                break
            continue

    if content_length < 0:
        return None

    body = _stdin.read(content_length)
    return json.loads(body.decode("utf-8"))


def _read() -> dict | None:
    """Read a JSON-RPC message from stdin, auto-detecting the transport.

    First call peeks at the first line to determine whether the client uses
    NDJSON (Codex, rmcp) or Content-Length framing (Cursor, Claude Code).
    Subsequent calls use the detected transport directly.
    """
    global _transport

    if _transport == "ndjson":
        return _read_ndjson()
    if _transport == "content-length":
        return _read_content_length("")

    # First message — auto-detect transport
    raw_line = _stdin.readline()
    if not raw_line:
        return None

    line = raw_line.decode("utf-8", errors="replace").strip()
    if not line:
        # Blank line before any real data — assume Content-Length
        _transport = "content-length"
        return _read_content_length("")

    if line.startswith("{"):
        # NDJSON: the entire message is on this line
        _transport = "ndjson"
        return json.loads(line)
    else:
        # Content-Length framing: this line is a header
        _transport = "content-length"
        return _read_content_length(line)


TOOLS = [
    {
        "name": "stitch_list_tasks",
        "description": "List all tasks in the current project or globally. Use this to discover what tasks exist and find the active task.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "all_projects": {
                    "type": "boolean",
                    "description": "If true, list tasks across all projects",
                    "default": False,
                }
            },
        },
    },
    {
        "name": "stitch_get_task",
        "description": "Get full details of a task including objective, state, decisions, and recent snapshots. Use 'active' as task_id to get the currently active task.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {
                    "type": "string",
                    "description": "Task ID, or 'active' for the current task",
                }
            },
            "required": ["task_id"],
        },
    },
    {
        "name": "stitch_create_task",
        "description": "Create a new task with a title and objective. This also sets it as the active task.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Task title"},
                "objective": {"type": "string", "description": "Task objective"},
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Tags for categorization",
                },
            },
            "required": ["title"],
        },
    },
    {
        "name": "stitch_update_task",
        "description": "Update the current state, next steps, blockers, or status of a task.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "Task ID or 'active'"},
                "current_state": {"type": "string"},
                "next_steps": {"type": "string"},
                "blockers": {"type": "string"},
                "status": {"type": "string", "enum": ["active", "paused", "completed", "abandoned"]},
            },
            "required": ["task_id"],
        },
    },
    {
        "name": "stitch_snapshot",
        "description": "Save a snapshot of progress. WHEN: (1) After completing any sub-task or step, (2) After a failed experiment (prefix with 'FAILED:'), (3) Every 3-5 meaningful actions as a progress checkpoint, (4) Before session end. WHAT: Be specific — 'Implemented JWT auth with RS256, tokens expire in 1h' not 'worked on auth'. Duplicates are auto-rejected.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "Task ID or 'active'"},
                "message": {"type": "string", "description": "What was done + what was the result (min 10 chars, be specific)"},
            },
            "required": ["message"],
        },
    },
    {
        "name": "stitch_add_decision",
        "description": "Record a technical decision. WHEN: Any architectural, design, or implementation choice where alternatives existed. WHAT: The problem, what you chose, what you rejected, and why. Future agents use this to avoid repeating failed experiments. Duplicate decisions (same problem) are auto-rejected.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "Task ID or 'active'"},
                "problem": {"type": "string", "description": "The problem being solved (min 5 chars)"},
                "chosen": {"type": "string", "description": "The chosen solution (required)"},
                "alternatives": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Alternatives considered and rejected",
                },
                "tradeoffs": {"type": "string", "description": "Tradeoffs of the choice"},
                "reasoning": {"type": "string", "description": "Why this choice was made over alternatives"},
            },
            "required": ["problem", "chosen"],
        },
    },
    {
        "name": "stitch_get_handoff",
        "description": "Generate a compact handoff bundle for a task. Contains objective, state, decisions, and recent activity — everything needed to resume work.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "Task ID or 'active'"},
                "token_budget": {
                    "type": "integer",
                    "description": "Max tokens for the bundle (default 3000)",
                    "default": 3000,
                },
            },
        },
    },
    {
        "name": "stitch_search",
        "description": "Search for tasks by keyword across titles, objectives, tags, and decisions.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search keyword"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "stitch_get_context",
        "description": "Read the full context.md file for a task — the human-readable living document with all context.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "Task ID or 'active'"},
            },
        },
    },
    {
        "name": "stitch_auto_setup",
        "description": "Idempotent project bootstrap. Call this FIRST at the start of every session. It silently initializes Stitch, injects agent discovery files, and installs git hooks if needed. Safe to call multiple times — does nothing if already set up. Returns the active task if one exists.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "stitch_smart_match",
        "description": "Find existing tasks by relevance. Searches across title, decisions, snapshots, and files. Returns ranked results with confidence scores. Use this to find the right task to resume.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Natural-language description of the task to find. Use specific terms — rare words are better signals than common ones."},
            },
            "required": ["query"],
        },
    },
    {
        "name": "stitch_resume_briefing",
        "description": "Generate a structured briefing for resuming a task. Includes warnings about dead ends, architecture decisions with reasoning, exact next steps, and live repo state. Read this BEFORE writing any code.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "Task ID or 'active'"},
            },
        },
    },
    {
        "name": "stitch_auto_route",
        "description": "THE PRIMARY ENTRY POINT — call this FIRST when a user starts a session. Pass the user's message here. Stitch will: (1) auto-setup the project, (2) detect resume vs new, (3) find matching task by relevance, (4) return saved context. IMPORTANT: Tell the user what happened. During the session, push updates via stitch_snapshot (after each step), stitch_add_decision (after choices), and stitch_checkpoint (before ending session or context summarization).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "user_prompt": {"type": "string", "description": "The user's message or task description"},
            },
            "required": ["user_prompt"],
        },
    },
    {
        "name": "stitch_checkpoint",
        "description": "Save a rich checkpoint. WHEN: (1) Before ending your session, (2) Before context/chat summarization, (3) After completing a major milestone, (4) When you sense context is getting large. This is the MOST IMPORTANT push — it captures everything (summary, decisions, experiments, failures, open questions) in one call. Survives restarts and context exhaustion.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "Task ID or 'active'"},
                "summary": {"type": "string", "description": "What was accomplished in this session (be comprehensive)"},
                "decisions_made": {"type": "string", "description": "Key decisions: what was chosen, what was rejected, why"},
                "experiments": {"type": "string", "description": "What was tried — both successful approaches and unsuccessful ones"},
                "failures": {"type": "string", "description": "Dead ends: what failed, why, so next agent avoids them"},
                "open_questions": {"type": "string", "description": "Unresolved issues, things to investigate, pending items"},
            },
            "required": ["summary"],
        },
    },
]


class StitchServer:
    """Minimal MCP server for Stitch.

    Uses lazy Store initialization: the MCP handshake (initialize, tools/list)
    responds instantly without touching the filesystem. The Store is only created
    when a tool is actually called, keeping startup time < 10ms.
    """

    def __init__(self, project_path: str | None = None):
        self._project_path = project_path
        self._store: "Store | None" = None

    @property
    def store(self) -> "Store":
        """Lazily create the Store on first access."""
        if self._store is None:
            from .store import Store
            self._store = Store(self._project_path)
            self._store.init_project()
        return self._store

    def _resolve_task_id(self, task_id: str | None) -> str | None:
        if not task_id or task_id == "active":
            return self.store.get_active_task_id()
        return task_id

    def handle_request(self, msg: dict) -> dict:
        method = msg.get("method", "")
        params = msg.get("params", {})
        req_id = msg.get("id")

        if method == "initialize":
            # Echo the client's protocol version for maximum compatibility.
            # We support both 2024-11-05 (Content-Length) and 2025-06-18 (NDJSON).
            client_version = params.get("protocolVersion", "2024-11-05")
            return self._response(req_id, {
                "protocolVersion": client_version,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "xstitch", "version": "0.3.4"},
            })

        elif method == "notifications/initialized":
            return None

        elif method == "tools/list":
            return self._response(req_id, {"tools": TOOLS})

        elif method == "tools/call":
            tool_name = params.get("name", "")
            args = params.get("arguments", {})
            try:
                result = self._call_tool(tool_name, args)
                return self._response(req_id, {
                    "content": [{"type": "text", "text": result}]
                })
            except Exception as e:
                return self._error(req_id, -32000, str(e))

        elif method == "ping":
            return self._response(req_id, {})

        return self._error(req_id, -32601, f"Method not found: {method}")

    def _call_tool(self, name: str, args: dict) -> str:
        if name == "stitch_list_tasks":
            tasks = self.store.list_tasks(
                project_only=not args.get("all_projects", False)
            )
            if not tasks:
                return "No tasks found. Create one with stitch_create_task."
            active = self.store.get_active_task_id()
            lines = []
            for t in tasks:
                marker = " (ACTIVE)" if t.id == active else ""
                lines.append(f"[{t.status}] {t.id} — {t.title}{marker}")
            return "\n".join(lines)

        elif name == "stitch_get_task":
            task_id = self._resolve_task_id(args.get("task_id"))
            if not task_id:
                return "No active task. Create one with stitch_create_task."
            task = self.store.get_task(task_id)
            if not task:
                return f"Task {task_id} not found."
            snaps = self.store.get_snapshots(task_id, limit=3)
            decs = self.store.get_decisions(task_id)
            lines = [
                f"# {task.title} ({task.id})",
                f"Status: {task.status}",
                f"Objective: {task.objective or '(not set)'}",
                f"Current State: {task.current_state or '(not set)'}",
                f"Next Steps: {task.next_steps or '(not set)'}",
                f"Blockers: {task.blockers or '(none)'}",
            ]
            if decs:
                lines.append(f"\nDecisions ({len(decs)}):")
                for d in decs[-5:]:
                    lines.append(f"  - {d.problem} -> {d.chosen}")
            if snaps:
                lines.append(f"\nRecent snapshots ({len(snaps)}):")
                for s in snaps:
                    lines.append(f"  [{s.timestamp}] {s.message[:80]}")
            return "\n".join(lines)

        elif name == "stitch_create_task":
            task = self.store.create_task(
                title=args["title"],
                objective=args.get("objective", ""),
                tags=args.get("tags", []),
            )
            return f"Created task: {task.id} — {task.title} (now active)"

        elif name == "stitch_update_task":
            task_id = self._resolve_task_id(args.get("task_id"))
            if not task_id:
                return "No active task."
            task = self.store.get_task(task_id)
            if not task:
                return f"Task {task_id} not found."
            if "current_state" in args:
                task.current_state = args["current_state"]
            if "next_steps" in args:
                task.next_steps = args["next_steps"]
            if "blockers" in args:
                task.blockers = args["blockers"]
            if "status" in args:
                task.status = args["status"]
            self.store.update_task(task)
            self.store.update_context_file(task_id)
            return f"Task {task_id} updated."

        elif name == "stitch_snapshot":
            task_id = self._resolve_task_id(args.get("task_id"))
            if not task_id:
                return "No active task."
            from .capture import capture_snapshot
            snap = capture_snapshot(
                message=args.get("message", ""),
                source="agent",
                cwd=str(self.store.project_path),
                task_id=task_id,
            )
            rejection = self.store.add_snapshot(task_id, snap)
            if rejection:
                return rejection
            self.store.update_context_file(task_id)
            return f"Snapshot saved: {snap.message[:80]}"

        elif name == "stitch_add_decision":
            task_id = self._resolve_task_id(args.get("task_id"))
            if not task_id:
                return "No active task."
            from .models import Decision
            decision = Decision(
                task_id=task_id,
                problem=args["problem"],
                chosen=args["chosen"],
                alternatives=args.get("alternatives", []),
                tradeoffs=args.get("tradeoffs", ""),
                reasoning=args.get("reasoning", ""),
            )
            rejection = self.store.add_decision(task_id, decision)
            if rejection:
                return rejection
            self.store.update_context_file(task_id)
            return f"Decision logged: {decision.problem} -> {decision.chosen}"

        elif name == "stitch_get_handoff":
            task_id = self._resolve_task_id(args.get("task_id"))
            if not task_id:
                return "No active task."
            bundle = self.store.build_handoff(
                task_id,
                token_budget=args.get("token_budget", 3000),
            )
            if not bundle:
                return f"Task {task_id} not found."
            return bundle.to_markdown()

        elif name == "stitch_search":
            results = self.store.search_tasks(args["query"])
            if not results:
                return f"No tasks matching '{args['query']}'."
            lines = [f"Found {len(results)} task(s):"]
            for t in results:
                lines.append(f"  [{t.status}] {t.id} — {t.title}")
            return "\n".join(lines)

        elif name == "stitch_auto_setup":
            from .intelligence import auto_setup
            result = auto_setup(str(self.store.project_path), quiet=True)
            parts = ["Stitch project ready."]
            if result["actions"]:
                parts.append(f"Setup actions: {', '.join(result['actions'])}")
            if result.get("active_task_id"):
                parts.append(f"Active task: {result['active_task_id']} — {result.get('active_task_title', '?')}")
            else:
                parts.append("No active task. Create one with the stitch_create_task MCP tool or run: python3 -m xstitch.cli task new \"title\"")
            return " ".join(parts)

        elif name == "stitch_smart_match":
            from .intelligence import smart_match
            results = smart_match(args["query"], self.store)
            if not results:
                return f"No tasks matching '{args['query']}'."
            lines = [f"Found {len(results)} task(s) by BM25 relevance:\n"]
            for r in results:
                t = r["task"]
                evidence = r.get("evidence", [])
                field_scores = r.get("field_scores", {})
                lines.append(f"[{r['confidence']:.0%}] {t.id} — {t.title}")
                if evidence:
                    lines.append(f"  Evidence: {', '.join(evidence[:6])}")
                if field_scores:
                    top = sorted(field_scores.items(), key=lambda x: -x[1])[:3]
                    lines.append(f"  Top fields: {', '.join(f'{k}={v:.1f}' for k,v in top)}")
                if t.objective:
                    lines.append(f"  Objective: {t.objective[:100]}")
                lines.append("")
            return "\n".join(lines)

        elif name == "stitch_resume_briefing":
            from .relevance import generate_resume_briefing
            task_id = self._resolve_task_id(args.get("task_id"))
            if not task_id:
                return "No active task."
            return generate_resume_briefing(task_id, self.store)

        elif name == "stitch_auto_route":
            from .intelligence import auto_route, format_auto_route_response
            result = auto_route(args["user_prompt"], self.store)
            return format_auto_route_response(result)

        elif name == "stitch_get_context":
            task_id = self._resolve_task_id(args.get("task_id"))
            if not task_id:
                return "No active task."
            ctx_file = self.store.tasks_dir / task_id / "context.md"
            if ctx_file.exists():
                return ctx_file.read_text()
            return f"No context file for task {task_id}."

        elif name == "stitch_checkpoint":
            from .capture import capture_pre_summarize_snapshot
            task_id = self._resolve_task_id(args.get("task_id"))
            if not task_id:
                return "No active task."
            snap = capture_pre_summarize_snapshot(
                summary=args.get("summary", ""),
                decisions_made=args.get("decisions_made", ""),
                experiments=args.get("experiments", ""),
                failures=args.get("failures", ""),
                open_questions=args.get("open_questions", ""),
                cwd=str(self.store.project_path),
                task_id=task_id,
            )
            rejection = self.store.add_snapshot(task_id, snap)
            if rejection:
                return rejection
            self.store.update_context_file(task_id)
            self.store.build_handoff(task_id)
            return (
                f"Checkpoint saved to disk. Summary, decisions, experiments, and failures "
                f"are now persisted and will survive context summarization and restarts. "
                f"Handoff bundle regenerated."
            )

        return f"Unknown tool: {name}"

    @staticmethod
    def _response(req_id, result):
        return {"jsonrpc": "2.0", "id": req_id, "result": result}

    @staticmethod
    def _error(req_id, code, message):
        return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


def run_server(project_path: str | None = None):
    server = StitchServer(project_path)

    # Signal readiness on stderr — Cursor and other MCP hosts wait for this
    # before sending the initialize message over stdin.
    sys.stderr.write("Stitch MCP Server running on stdio\n")
    sys.stderr.flush()

    while True:
        msg = _read()
        if msg is None:
            break

        response = server.handle_request(msg)
        if response is not None:
            _send(response)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", default=None, help="Project path")
    args = parser.parse_args()
    run_server(args.project)
