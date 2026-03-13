# Stitch v0.4.0 — Architecture, Approaches & Tradeoffs

## Problem Statement

When using multiple AI tools (Cursor, Claude Code, Codex, Antigravity, etc.), context is lost on tool switch due to session/token limits. The new agent must rediscover decisions, experiments, and state — wasting tokens and often repeating mistakes.

---

## Existing Open-Source Solutions (March 2026)

| Tool | Approach | Pros | Cons |
|------|----------|------|------|
| **UltraContext** | Node.js daemon, auto-capture sessions, MCP server | Real-time capture, cross-agent, versioned | Requires Node >= 22, daemon always running, heavier |
| **DevContext** | npm CLI, save/resume, git hooks, MCP | Team features (handoff, share), MCP integration | npm dependency, session-focused (not task-focused) |
| **Spec Smith** | `.specs/` markdown directory, Claude Code plugin | Persistent specs survive sessions | Claude Code plugin only, no multi-tool |
| **Cross-Agent Session Resumer** | Converts chat formats between tools via canonical IR | Works with raw exports | Noisy (full chat), token-expensive |
| **Work Context Protocol** | Standard API for reading/writing project context | Standard-based | Early stage, limited adoption |
| **agent-handoff** (GitHub) | Markdown handoff files in repo | Simple, file-based | Manual, no automation, no task isolation |

### What's missing from all of them

1. **Task isolation** — Most solutions treat context as one blob per project, not per task
2. **Automatic periodic capture** — Few auto-capture without manual triggers
3. **Agent auto-discovery** — No solution auto-configures CLAUDE.md, .cursorrules, Copilot instructions
4. **Token-budget awareness** — Handoff bundles aren't size-limited; can blow context windows
5. **Zero-dependency Python** — Most require Node.js or external services

---

## Stitch v0.4.0 — Architecture

### Module Structure

```
xstitch/
├── models.py            # Task, Snapshot, Decision dataclasses
├── store.py             # Machine-local storage, TTL cleanup, migration
├── capture.py           # Git state capture, token-aware truncation
├── log.py               # Structured [Stitch OK/WARNING/ERROR] logging
├── cli.py               # CLI (20 commands)
├── mcp_server.py        # MCP server (14 tools, dual-protocol, lazy init)
├── global_setup.py      # OOP tool registry + MCP/instruction injection
├── intelligence.py      # Intent detection, auto-routing, smart matching
├── relevance.py         # Legacy BM25 (still used by intelligence.py)
├── discovery.py         # Project-level agent config injection
├── healthcheck.py       # Broken install detection
├── doctor.py            # 18-check diagnostics across 5 categories
├── enforcement.py       # Claude Code hooks + Cursor alwaysApply
├── hooks.py / daemon.py / launchd.py  # Automation
│
├── search/              # Enhanced search engine (Phase 1)
│   ├── __init__.py      # Unified SearchEngine facade + RRF fusion
│   ├── tokenizer.py     # Stemming, aliases, bigrams, stop words
│   ├── bm25.py          # BM25 Okapi with hierarchical field weights
│   ├── fuzzy.py         # Trigram Jaccard fuzzy matching
│   ├── embeddings.py    # Optional sentence-transformers (guarded)
│   └── index.py         # Persistent JSON index with mtime staleness
│
├── core/                # Re-export shims (backward compat)
├── integrations/        # Re-export shims + per-tool shims
│   └── tools/           # Entry-point discoverable tool classes
├── mcp/                 # Re-export shims for MCP server
├── diagnostics/         # Re-export shims for doctor/healthcheck
└── automation/          # Re-export shims for hooks/daemon/launchd
```

Canonical code lives in top-level files. Subpackages are re-export shims
that provide clean import paths (`from xstitch.core import Task`) without
breaking `unittest.mock.patch("xstitch.store.GLOBAL_HOME")`.

### System Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    AI Tools Layer                        │
│  Cursor │ Claude Code │ Codex │ Gemini │ Windsurf │ ... │
└─────┬──────────┬───────────┬──────────┬────────────┬────┘
      │          │           │          │            │
      ▼          ▼           ▼          ▼            ▼
┌─────────────────────────────────────────────────────────┐
│              Stitch Interface Layer                        │
│                                                         │
│  ┌─────────┐  ┌───────────┐  ┌──────────────────────┐  │
│  │   CLI   │  │MCP Server │  │ Agent Auto-Discovery │  │
│  │(20 cmds)│  │(14 tools, │  │ (CLAUDE.md,          │  │
│  │         │  │ NDJSON +  │  │  .cursorrules,       │  │
│  │         │  │ Content-  │  │  AGENTS.md, etc.)    │  │
│  │         │  │ Length)   │  │                      │  │
│  └────┬────┘  └─────┬─────┘  └──────────┬───────────┘  │
│       │             │                    │              │
│       ▼             ▼                    ▼              │
│  ┌──────────────────────────────────────────────────┐   │
│  │              Core Engine                          │   │
│  │  Task Manager │ Snapshot Engine │ Decision Log   │   │
│  │  Handoff Builder │ Token Budgeter               │   │
│  └──────────────────────┬───────────────────────────┘   │
│                         │                               │
│  ┌──────────────────────▼───────────────────────────┐   │
│  │         Search Engine (xstitch/search/)              │   │
│  │  BM25 Okapi │ Trigram Fuzzy │ Optional Embeddings│   │
│  │  Persistent Index │ RRF Score Fusion             │   │
│  └──────────────────────┬───────────────────────────┘   │
│                         │                               │
│  ┌──────────────────────▼───────────────────────────┐   │
│  │              Storage Layer                        │   │
│  │  ~/.stitch/projects/<key>/  │  Global registry     │   │
│  │  Per-task isolation       │  TTL auto-cleanup     │   │
│  └──────────────────────────────────────────────────┘   │
│                                                         │
│  ┌──────────────────────────────────────────────────┐   │
│  │       Tool Integration Layer (extensible)         │   │
│  │  OOP registry │ Entry-point plugins │ Skills     │   │
│  │  JSON MCP │ TOML MCP │ CLI MCP │ Instructions   │   │
│  └──────────────────────────────────────────────────┘   │
│                                                         │
│  ┌──────────────────────────────────────────────────┐   │
│  │           Automation & Diagnostics                │   │
│  │  Git Hooks │ Daemon │ LaunchAgent │ Doctor (18)  │   │
│  └──────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────┘
```

### Key Design Decisions (v0.3.0 - v0.4.0)

| Decision | Chosen | Alternatives Considered | Why |
|----------|--------|------------------------|-----|
| Module layout | Top-level canonical + subpackage shims | Move code to subpackages | `unittest.mock.patch` breaks when patched references diverge from runtime references |
| MCP transport | Auto-detect NDJSON vs Content-Length from first byte | Force one protocol | Codex/rmcp uses NDJSON; Cursor/Claude use Content-Length; auto-detection is transparent |
| Search fusion | Reciprocal Rank Fusion (RRF) | Linear score combination | RRF is parameter-free and robust across different score distributions |
| Fuzzy matching | Trigram Jaccard | Levenshtein | O(1) vocabulary lookup after build; Levenshtein is O(n*m) per comparison |
| Plugin system | Python entry_points | Config file registry | Standard, works with pip, no config files to manage |
| TTL cleanup | 45-day auto with cooldown | Manual only | Prevents unbounded disk growth without user intervention |
| Skills support | Optional per-tool mixin | Separate skills manager | Keeps it simple; not all tools support skills |

### Five Approaches Implemented (A–E)

#### Approach A: File-Based Task Context (.stitch/ directory)

**How it works**: Each task gets isolated storage under `.stitch/tasks/<task-id>/` with structured JSON + Markdown files. Context lives in the repo, is git-tracked, and readable by any tool.

**Files per task:**
- `meta.json` — Task metadata (title, objective, status, tags)
- `snapshots.json` — Timestamped state captures
- `decisions.json` — ADR-style decision records
- `context.md` — Auto-generated human/agent-readable living document
- `handoff.md` — Token-budget-aware handoff bundle

| Metric | Rating | Notes |
|--------|--------|-------|
| Feasibility | 10/10 | Zero dependencies, just files |
| Robustness | 9/10 | Git-tracked, versioned, recoverable |
| Reliability | 8/10 | Depends on agent discipline |
| Task Isolation | 10/10 | Each task is a separate folder |
| Token Efficiency | 9/10 | Structured, compact by design |

**Tradeoffs:**
- (+) Works everywhere, no setup beyond `stitch init`
- (+) PR-reviewable, team-shareable via git
- (-) Requires manual or automated triggers to update
- (-) No real-time capture of agent conversations

---

#### Approach B: CLI with Auto-Capture

**How it works**: Rich CLI (`stitch snap`, `stitch decide`, `stitch handoff`) that automatically captures git state (branch, diff, status, recent commits) and combines it with human-provided context.

**Key commands:**
```bash
stitch task new "title" -o "objective"    # Create isolated task
stitch snap -m "what just happened"       # Auto-capture git + message
stitch decide -p "problem" -c "chosen"    # Log decision with tradeoffs
stitch handoff                            # Generate compact bundle
stitch resume                             # Generate paste-ready prompt
stitch search "keyword"                   # Find tasks across projects
```

| Metric | Rating | Notes |
|--------|--------|-------|
| Feasibility | 10/10 | `pip install -e .` and ready |
| Robustness | 9/10 | Auto-captures git state, bounded output |
| Reliability | 9/10 | Low-friction, token-aware truncation |
| Automation | 7/10 | Still needs manual trigger for most ops |
| Distribution | 9/10 | pip-installable, cross-platform |

**Tradeoffs:**
- (+) Frictionless — single commands, auto-captures git state
- (+) Token-budget-aware (truncates large diffs/logs)
- (-) User must remember to run commands during session
- (-) Cannot capture reasoning that only exists in chat history

---

#### Approach C: MCP Server (Native Tool Integration)

**How it works**: A JSON-RPC 2.0 MCP server (`xstitch.mcp_server`) that exposes 9 tools directly to any MCP-compatible AI tool. The agent can read/write context natively without CLI.

**MCP Tools exposed:**
1. `stitch_list_tasks` — Discover tasks
2. `stitch_get_task` — Get full task details
3. `stitch_create_task` — Create a new task
4. `stitch_update_task` — Update state/next steps/status
5. `stitch_snapshot` — Capture current state
6. `stitch_add_decision` — Log a decision
7. `stitch_get_handoff` — Get handoff bundle
8. `stitch_search` — Search tasks by keyword
9. `stitch_get_context` — Read the living context document

**Registration (Cursor):**
```json
{
    "mcpServers": {
        "xstitch": {
            "command": "python3",
            "args": ["-m", "xstitch.mcp_server"],
            "cwd": "/path/to/project"
        }
    }
}
```

| Metric | Rating | Notes |
|--------|--------|-------|
| Feasibility | 9/10 | Zero-dep MCP server, standard protocol |
| Robustness | 9/10 | Same storage engine as CLI |
| Reliability | 10/10 | Agent uses tools natively — no copy-paste |
| Automation | 10/10 | Agent can auto-snapshot during work |
| Distribution | 8/10 | Requires MCP-compatible tool |

**Tradeoffs:**
- (+) Agent uses context natively — no manual copy-paste
- (+) Agent can auto-capture snapshots and decisions during its session
- (+) Works in Cursor, Claude Code, Continue, and any MCP client
- (-) Not all tools support MCP yet (Codex, Antigravity may not)
- (-) Need to register the server in each tool's config

---

#### Approach D: Automatic Background Capture

**How it works**: Two automation mechanisms that capture context without any manual intervention.

**D1: Git Hooks (post-commit, post-checkout)**
Automatically snapshots after every commit and branch switch.

```bash
stitch hooks install    # One-time setup
# From now on, every git commit auto-snapshots
```

**D2: Background Daemon**
Periodically checks for significant file changes and auto-snapshots.

```bash
stitch daemon start --interval 300   # Every 5 minutes
stitch daemon stop
```

| Metric | Rating | Notes |
|--------|--------|-------|
| Feasibility | 9/10 | Git hooks are universal; daemon uses fork() |
| Robustness | 8/10 | Captures commits automatically |
| Reliability | 8/10 | Daemon may miss non-file-change activity |
| Automation | 10/10 | Completely hands-free |
| Noise | 6/10 | May capture insignificant changes |

**Tradeoffs:**
- (+) Zero manual effort after setup
- (+) Git hooks capture every commit with context
- (+) Daemon catches work-in-progress between commits
- (-) Cannot capture reasoning/decisions (only file changes)
- (-) Daemon uses `os.fork()` (Unix only; needs adaptation for Windows)
- (-) May accumulate many low-value snapshots

---

#### Approach E: Agent Auto-Discovery (CLAUDE.md / .cursorrules / PageIndex)

**How it works**: Automatically injects instructions into tool-specific config files so agents discover and use Stitch context without being told.

```bash
stitch inject
# Creates/updates: CLAUDE.md, .cursorrules, .cursor/rules/stitch-context.mdc,
#                  .github/copilot-instructions.md, .stitch/TASK_INDEX.md
```

**Discovery mechanisms:**
1. **CLAUDE.md** — Claude Code reads at session start
2. **.cursorrules** — Cursor reads for project rules
3. **.cursor/rules/** — Cursor rules directory (MDC format)
4. **.github/copilot-instructions.md** — GitHub Copilot
5. **TASK_INDEX.md** — Human/agent-readable index of all tasks
6. **task_index.json** — Machine-readable index for programmatic search

| Metric | Rating | Notes |
|--------|--------|-------|
| Feasibility | 10/10 | Standard files that tools already read |
| Robustness | 9/10 | Each tool has its own config format |
| Reliability | 9/10 | Agent is instructed at session start |
| Coverage | 8/10 | Covers major tools; some have no config hook |
| Maintenance | 7/10 | Must re-run inject when tasks change |

**Tradeoffs:**
- (+) Agent automatically knows about Stitch without being told
- (+) Works with Claude Code, Cursor, Copilot out-of-the-box
- (+) PageIndex gives agents a searchable directory of tasks
- (-) Some tools (Codex CLI, Antigravity) don't have config file hooks
- (-) Injected instructions consume baseline context tokens

---

## How an Agent Discovers Context (3 Techniques)

### Technique 1: TaskID-Based Lookup
User tells the agent: "Resume task `abc123`"
Agent calls `stitch_get_task(task_id="abc123")` or reads `.stitch/tasks/abc123/context.md`

### Technique 2: PageIndex Search
Agent reads `.stitch/TASK_INDEX.md` or `.stitch/task_index.json` and browses tasks by title/tags/status. Picks the relevant one automatically.

### Technique 3: Auto-Discovery via Config Files
Agent starts session → reads CLAUDE.md / .cursorrules → sees Stitch instructions → reads active task → begins work with full context. **Zero human intervention.**

---

## Comparison with ChatGPT POC and Antigravity POC

| Feature | ChatGPT POC | Antigravity POC | Stitch v0.2.0 |
|---------|-------------|-----------------|-------------|
| Task isolation | Proposed (.handoff/task-id/) | None (single .agent/) | Per-task folders |
| Automation | Proposed but not built | None (manual CLI) | Git hooks + daemon |
| MCP integration | Proposed as future | None | Working MCP server (9 tools) |
| Agent auto-discovery | Not addressed | Not addressed | CLAUDE.md, .cursorrules, Copilot, PageIndex |
| Token budget | Mentioned (1-3k target) | Not implemented | Built-in truncation + budget param |
| Global registry | Not addressed | Not addressed | ~/.stitch/registry.json |
| Cross-project search | Not addressed | Not addressed | `stitch search` + `stitch task list --all` |
| Distribution | Suggested pip | Not addressed | pip-installable |

---

## Recommended Setup (Comprehensive)

For maximum coverage, use all approaches together:

```bash
# 1. Install Stitch
pip install -e /path/to/AgentHandOffAndContextProtocol

# 2. Initialize in your project
cd /your/project
python3 -m xstitch.cli init

# 3. Set up automation
python3 -m xstitch.cli hooks install    # Git hooks
python3 -m xstitch.cli daemon start     # Background daemon (optional)

# 4. Inject agent discovery
python3 -m xstitch.cli inject           # CLAUDE.md, .cursorrules, etc.

# 5. Register MCP server (in Cursor settings or claude_desktop_config.json)
# See MCP registration section above

# 6. Create your first task
python3 -m xstitch.cli task new "My task" -o "What I want to achieve"
```

After this setup:
- **Git hooks** auto-capture on every commit
- **Daemon** captures periodically between commits  
- **MCP server** lets agents natively read/write context
- **Config injection** makes agents discover Stitch automatically
- **CLI** is always available for manual operations

---

## Feasibility & Effort Summary

| Approach | Effort to POC | Effort to Production | Value |
|----------|---------------|---------------------|-------|
| A: File-based | 1 day | 1 week | High (foundation) |
| B: CLI | 2 days | 1 week | High (daily use) |
| C: MCP Server | 2 days | 2 weeks | Very High (native agent access) |
| D: Auto-capture | 1 day | 1 week | High (zero-effort context) |
| E: Agent Discovery | 1 day | 1 week | High (zero-config for agents) |
| **All combined** | **~5 days** | **~3 weeks** | **Maximum coverage** |

---

*Document updated for Stitch v0.4.0 — March 2026*
