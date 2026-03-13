# Stitch CLI Reference

All commands can be run as `stitch <command>` (if the CLI is in your PATH) or `python3 -m xstitch.cli <command>`.

---

## Setup Commands

### `stitch global-setup`

One-time setup that detects all AI tools on your machine and configures them.

```bash
stitch global-setup              # Configure everything
stitch global-setup --dry-run    # Preview without making changes
```

Auto-configures: Cursor, Claude Code, Codex, Gemini CLI, Windsurf, Copilot CLI, Zed, Continue.dev, Aider.

### `stitch auto-setup`

Initialize Stitch in the current project directory.

```bash
cd /your/project
stitch auto-setup
```

Injects protocol instructions into project-level config files and initializes task storage at `~/.stitch/projects/<project-key>/`.

### `stitch inject`

Force-inject instruction files for all tools (or specific ones).

```bash
stitch inject --all              # All detected tools
stitch inject --tool cursor      # Specific tool
```

---

## Intelligent Routing

### `stitch auto "<prompt>"`

The core command. Detects user intent, searches for matching tasks, and returns structured context.

```bash
stitch auto "resume the database migration"    # Finds matching task
stitch auto "build a REST API for users"       # Creates new task
stitch auto "hi"                               # No context loaded (conversational)
```

Returns a JSON response with the action taken (`resumed`, `created`, `greeting`, etc.) and any context to inject.

---

## Task Management

### `stitch task new "<title>"`

Create a new task.

```bash
stitch task new "Implement user authentication"
```

### `stitch task list`

List all tasks in the current project.

```bash
stitch task list
stitch task list --all           # All projects
```

### `stitch task show [--id <task-id>]`

Show details of the active task (or a specific one).

```bash
stitch task show
stitch task show --id abc123
```

---

## Context Capture

### `stitch snap -m "<message>"`

Capture a snapshot of the current state.

```bash
stitch snap -m "implemented the login endpoint"
stitch snap -m "RESULT: auth tests passing" --source daemon
```

### `stitch decide`

Record a decision with alternatives and reasoning.

```bash
stitch decide \
  -p "Which auth library?" \
  -c "PyJWT" \
  -a "authlib, python-jose" \
  -r "Lighter weight, better maintained, our API is simple"
```

### `stitch checkpoint`

Rich checkpoint for pre-summarization (before context window fills up).

```bash
stitch checkpoint \
  -s "Auth system complete: JWT + refresh tokens" \
  -d "Chose PyJWT over authlib" \
  -e "Tested bcrypt vs argon2 — argon2 slower but more secure" \
  -f "Redis session store too complex for MVP" \
  -q "Should we add rate limiting now or later?"
```

---

## Context Retrieval

### `stitch resume [task-id]`

Generate a structured resume briefing for an agent.

```bash
stitch resume                    # Active task
stitch resume abc123             # Specific task
```

### `stitch handoff [task-id]`

Generate a compact handoff bundle.

```bash
stitch handoff
```

### `stitch smart-match "<query>"`

BM25 relevance search across all tasks.

```bash
stitch smart-match "database migration"
```

### `stitch search "<query>"`

Keyword search across tasks.

```bash
stitch search "auth"
```

---

## Diagnostics

### `stitch doctor`

Check installation health and suggest fixes.

```bash
stitch doctor
```

### `stitch daemon`

Background auto-snapshot daemon.

```bash
stitch daemon start --interval 300   # Every 5 minutes
stitch daemon stop
stitch daemon status
```

### `stitch launchd`

macOS-only: install a persistent launchd agent.

```bash
stitch launchd install --interval 600
stitch launchd status
stitch launchd uninstall
```

---

## MCP Server

The MCP server runs as a stdio process, launched automatically by configured tools.

```bash
python3 -u -m xstitch.mcp_server                          # stdio mode
python3 -u -m xstitch.mcp_server --project /path/to/proj  # specific project
```

### MCP Tools Exposed

| Tool | Description |
|------|-------------|
| `stitch_list_tasks` | List all tasks in the project |
| `stitch_get_task` | Get full task details |
| `stitch_create_task` | Create a new task |
| `stitch_snapshot` | Capture a snapshot |
| `stitch_add_decision` | Record a decision |
| `stitch_update_task` | Update task state/blockers |
| `stitch_checkpoint` | Rich checkpoint |
| `stitch_get_handoff` | Get handoff bundle |
| `stitch_resume_briefing` | Get resume briefing |
| `stitch_smart_match` | BM25 relevance search |
| `stitch_get_context` | Read the living context document |

The server auto-detects the transport protocol: NDJSON (used by Codex) or Content-Length framing (used by Cursor, Claude Code, and most others).
