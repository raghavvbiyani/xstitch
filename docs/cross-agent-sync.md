# Cross-Agent Sync — Design and Usage

> **Status:** Implemented in Stitch v0.4.0.
> **Scope:** Multi-agent environments where Cursor, Claude Code, the CLI, and
> other MCP hosts share the same Stitch installation but may resolve different
> "project scopes" from their own current working directory.

## The problem we solved

Before this work, `Store.__init__` resolved the project root with a plain
`Path(project_path or os.getcwd()).resolve()`. That single line produced
three different answers in the wild:

| Invoked by | Effective cwd | Resulting `project_key` |
|---|---|---|
| Cursor MCP (global `~/.cursor/mcp.json`) | user's home (Cursor's spawn default) | `raghavbiyani-2a3e97ed` |
| Claude Code MCP | the repo root | `myrepo-51b648d9` |
| `python3 -m xstitch.cli` from a repo shell | the repo root | `myrepo-51b648d9` |

A task written by Cursor landed under the home-directory scope. When Claude
(or the CLI) tried to read it from the repo, it looked at a *different*
scope directory and reported "task not found" even though the task was
present in `registry.json`. This was the exact failure that produced
orphaned task `1d6a4773d4f7`.

## Architecture

```
                             ┌──────────────────────┐
                             │ project_resolver.py  │   override > env > ancestor-walk > cwd
                             └──────────┬───────────┘
             ┌──────────────────────────┼──────────────────────────┐
             │                          │                          │
┌─────────────────────┐     ┌─────────────────────┐      ┌──────────────────┐
│ Cursor MCP (stdio)  │     │ Claude Code MCP     │      │    stitch CLI    │
│ AHCP_PROJECT_PATH=  │     │ AHCP_PROJECT_PATH=  │      │ (cwd = repo)     │
│ ${workspaceFolder}  │     │ ${workspaceFolder}  │      │                  │
└──────────┬──────────┘     └──────────┬──────────┘      └────────┬─────────┘
           │                           │                          │
           └─── all three resolve to the same ``project_key`` ────┘
                                      │
                                      ▼
                ┌──────────────────────────────────────────┐
                │ Store(project_path)                      │
                │  ├── get_task → local fast path          │
                │  ├── read-through via registry on miss   │
                │  └── mutations append to event log       │
                └────────────────────┬─────────────────────┘
                                     │
        ┌─────────────────────┬──────┴──────────────────────┐
        ▼                     ▼                             ▼
 ~/.ahcp/projects/     ~/.ahcp/registry.json        ~/.ahcp/events.jsonl
 <key>/tasks/<id>/     (file-locked r/m/w)          (append-only, locked)
        │                     │                             │
        │                     │                             ▼
        │                     │                    stitch_what_changed tool
        │                     │                    stitch events CLI
        │                     │                    ~/.ahcp/cursors/<agent>.json
        │                     │
        ▼                     ▼
 ``stitch doctor --repair`` detects scope mismatches and moves task
 directories between project scopes atomically, updating the registry
 and emitting ``task_moved`` events.
```

## Resolution order (single source of truth)

Implemented in
[`xstitch/project_resolver.py`](../xstitch/project_resolver.py). Every
entrypoint (`Store.__init__`, `cli.main`, `mcp_server.run_server`) goes
through this helper:

1. **Explicit override** (e.g. `--project` CLI flag, `Store(project_path=...)`)
2. **`AHCP_PROJECT_PATH` env var**, if it points to an existing directory
3. **Ancestor walk** from `cwd` looking for the first of:
   * `.git` (directory *or* file; git worktrees store a file)
   * `.ahcp` (directory *or* sentinel file — see `stitch init --pin`)
   * The walk skips the user's home directory explicitly; a marker inside
     `~/` would otherwise silently become a catch-all bucket.
4. **Fallback:** `cwd` as-is. The home directory is never auto-selected.

### MCP host configuration

`xstitch/global_setup.py` now injects the `env` block into every tool's MCP
config entry:

```json
{
  "command": "/path/to/python3",
  "args": ["-u", "-m", "xstitch.mcp_server"],
  "env": {
    "AHCP_PROJECT_PATH": "${workspaceFolder}",
    "AHCP_AGENT": "cursor"
  }
}
```

Hosts that expand `${workspaceFolder}` (Cursor, Windsurf, Zed, Claude Code)
pass the real path through the env. Hosts that do not expand it pass the
literal string which the resolver detects as invalid and falls back to the
ancestor walk. Safe in either case.

`AHCP_AGENT` tags event-log entries so downstream agents can filter by
writer identity.

### Pinning a non-git project root

For non-git projects, run `stitch init --pin` to drop a sentinel file named
`.ahcp` at the desired project root. The resolver's ancestor walk picks
it up the same way it picks up `.git`.

## Cross-project read-through

Every read API on `Store` now falls back to the global `registry.json`
when a task is not present in the current project scope:

| Method | Local path | Foreign fallback |
|---|---|---|
| `get_task` | `tasks/<id>/meta.json` | open owner project via registry, read there |
| `get_snapshots` | `tasks/<id>/snapshots.json` | same |
| `get_decisions` | `tasks/<id>/decisions.json` | same |
| `build_handoff` | writes under owner | writes under owner |
| `update_context_file` | writes under owner | writes under owner |

A new helper `Store.for_task(task_id) -> Store | None` returns a transient
`Store` rooted at the owning project for callers that need many sibling
reads (e.g. BM25 indexing).

## Registry concurrency

Two MCP servers writing simultaneously used to lose updates because
`_register_task` performed an unlocked read-modify-write on
`~/.ahcp/registry.json`. We now wrap every registry mutation in a
cross-platform advisory file lock (`xstitch/locks.py`, using `fcntl.flock` on
POSIX, `msvcrt.locking` on Windows). A 5s timeout guarantees forward
progress even if a peer is stuck.

See `tests/integration/test_cross_project_readthrough.py::
TestRegistryConcurrency::test_parallel_writers_do_not_lose_registry_entries`
for the regression test.

## Global event log

Every mutation that matters for cross-agent discovery emits an append-only
event to `~/.ahcp/events.jsonl`:

| Event type | Emitted from |
|---|---|
| `task_created` | `Store.create_task` |
| `task_updated` | `Store.update_task` |
| `snapshot_added` | `Store.add_snapshot` |
| `decision_added` | `Store.add_decision` |
| `context_updated` | `Store.update_context_file` |
| `handoff_built` | `Store.build_handoff` |
| `task_moved` | `repair.repair_orphan` |

### Event schema

```json
{
  "ts": "2026-04-20T07:14:18.123456+00:00",
  "seq": 4217,
  "event_type": "task_updated",
  "task_id": "1d6a4773d4f7",
  "project_path": "/abs/path/to/repo",
  "project_key": "repo-abc12345",
  "agent": "cursor",
  "meta": {"status": "active"}
}
```

* `seq` is a monotonically increasing integer stored at
  `~/.ahcp/.event_seq`, protected by the same lock primitive as the event
  file. Under heavy contention the implementation falls back to a
  microsecond timestamp so seq never collides across processes.
* `agent` defaults to `$AHCP_AGENT` (set by the MCP config) or `"unknown"`.

### Rotation

When `events.jsonl` grows past `EVENT_LOG_ROTATE_BYTES` (10 MB by default;
overridable via env for tests) the active file is renamed to
`events-YYYY-MM-DD-NNN.jsonl` and a fresh file starts. Readers can opt
into walking archives with `read_events(include_archives=True)`.

### "What changed since I last checked"

Two new MCP tools:

* `stitch_what_changed` — returns events filtered by `since`, `project`,
  `task_id`, `event_types`, etc. If `agent_id` is given, the tool
  automatically uses that agent's stored cursor as the default `since`.
* `stitch_mark_seen` — advances the cursor for an agent id. Typical flow:
  an agent calls `what_changed` at session start, processes the diff,
  then calls `mark_seen` at session end.

CLI counterparts are `stitch events` and `stitch mark-seen`.

### Per-agent cursors

Cursors are stored as one JSON file per agent at `~/.ahcp/cursors/<id>.json`
so different tools (Cursor, Claude Code, etc.) don't share a cursor.
Agent ids are sanitized so they cannot escape the cursors directory.

## Self-repair: `stitch doctor --repair`

`xstitch.repair.scan_orphans()` walks every task directory under
`~/.ahcp/projects/*/tasks/` and reports any of:

* `scope_mismatch` — directory's `project_key` disagrees with
  `meta.project_path`'s `project_key`.
* `no_marker` — `meta.project_path` no longer exists or has no
  `.git`/`.ahcp` marker.

`xstitch.repair.repair_orphan(task_id, new_project_path)` atomically:

1. Copies the task directory to the destination scope.
2. Rewrites `meta.project_path` at the destination.
3. Locks and updates the global registry entry.
4. Clears the source scope's `active_task` pointer if it named the moved
   task.
5. Removes the source directory (last — so a mid-run crash leaves the
   duplicate, not the gap).
6. Emits a `task_moved` event.

### Invocations

```bash
stitch doctor                  # includes a new "Orphaned tasks" check
stitch doctor --repair --dry-run   # preview moves without touching disk
stitch doctor --repair             # interactive prompts per orphan
stitch doctor --repair --yes       # auto-approve all suggested moves
```

Aborts destructively on any of these conditions (caller must intervene):

* Destination already has a task with the same id.
* `shutil.copytree` fails for any reason — the partial destination is
  removed so we never leave a half-copied task visible.

## Test matrix

| Suite | Count | Scope |
|---|---|---|
| `tests/unit/test_project_resolver.py` | 28 | Resolution rules, symlinks, sentinel |
| `tests/unit/test_event_log.py` | 21 | Append/read/filter/concurrency/rotation/corruption/cursors |
| `tests/unit/test_repair.py` | 12 | Detection + move happy/sad paths + doctor integration |
| `tests/integration/test_cross_project_readthrough.py` | 13 | Registry read-through, BM25 cross-project, concurrent subprocess writes |
| `tests/integration/test_cross_agent_sync.py` | 4 | End-to-end Cursor→Claude handoff scenario |

Full suite on this branch: **328 tests, 0 failures, 0 errors**.

## Upgrade notes for existing installs

* The next `stitch auto-setup` or `global-setup` run will idempotently
  update existing MCP config entries to include the new `env` block.
  Running `stitch doctor` shows "Updated stale config in <path>" once.
* Existing orphaned tasks become readable via cross-project read-through
  the moment you upgrade — no data loss. Run
  `stitch doctor --repair --dry-run` first to preview re-homing candidates.
* The registry and `events.jsonl` are created lazily, so no manual
  migration is needed.
