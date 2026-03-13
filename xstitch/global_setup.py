"""Global one-time setup for Stitch across all AI tools on a machine.

Run once: `python3 -m xstitch.cli global-setup`

Detects installed AI tools (Cursor, Claude Code, Windsurf, Codex, Gemini CLI,
Zed, Continue.dev, Aider, Copilot CLI) and configures each one so that Stitch is
available automatically in every session — no per-tool manual setup needed.

Integration strategy (layered — strongest mechanism available per tool):

  1. MCP server — native tool calling. Used for Cursor, Claude Code, Windsurf,
     Zed, Continue.dev, Codex, Gemini CLI, Copilot CLI.
  2. Instruction files — fallback for tools without MCP or as a complement.
     Used for Codex (AGENTS.md), Gemini (GEMINI.md), Aider (CONVENTIONS.md).
  3. Deterministic hooks — guaranteed execution (Claude Code UserPromptSubmit).
  4. Universal bootstrap — ~/.stitch/AGENT_BOOTSTRAP.md for unknown/future tools.

Tools that support MCP get BOTH MCP registration AND instruction files. The MCP
path gives agents native callable tools; the instruction file ensures the agent
knows about Stitch even if MCP fails to load. Never remove instruction files when
adding MCP — they're complementary, not competing.
"""

from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

from .discovery import Stitch_SECTION_MARKER, _inject_into_file

GLOBAL_HOME = Path.home() / ".stitch"

def _resolve_python_bin() -> str:
    """Resolve the most reliable python3 path, handling virtualenvs and pyenv."""
    candidates = [
        sys.executable,
        shutil.which("python3"),
        shutil.which("python"),
    ]
    for c in candidates:
        if c and os.path.isfile(c):
            resolved = os.path.realpath(c)
            if os.path.isfile(resolved):
                return resolved
    return "python3"


PYTHON_BIN = _resolve_python_bin()


MCP_SERVER_ENTRY = {
    "command": PYTHON_BIN,
    "args": ["-u", "-m", "xstitch.mcp_server"],
}


# ---------------------------------------------------------------------------
# OOP Tool Abstraction — each tool is a self-contained class
#
# Design: each ToolIntegration subclass owns its own detection, MCP injection,
# and instruction injection logic. Changing one tool (e.g. Codex's TOML format)
# cannot accidentally break another (e.g. Cursor's JSON config).
#
# Tools that support MCP get BOTH MCP registration AND instruction files.
# They're complementary: MCP gives native tool calls, instruction files ensure
# the agent knows Stitch even if MCP fails to load.
# ---------------------------------------------------------------------------

class ToolIntegration:
    """Base class for AI tool integrations. Each subclass encapsulates its own
    detection, MCP config injection, instruction file injection, and skills.

    Why a base class instead of a protocol or dict:
      - Subclasses can override only what differs (Open/Closed principle)
      - Common detection logic lives in mixins, not copy-pasted
      - entry_points plugin system requires a class to instantiate
      - isinstance checks work naturally for type-specific dispatch
    """

    name: str = ""

    def is_installed(self) -> tuple[bool, str]:
        """Check if this tool is installed. Returns (detected, method_description)."""
        return False, ""

    def inject_mcp(self, dry_run: bool = False) -> str | None:
        """Inject MCP server config for this tool. Returns description or None."""
        return None

    def inject_instructions(self, dry_run: bool = False) -> str | None:
        """Inject instruction files for this tool. Returns description or None."""
        return None

    def inject_skills(self, project_path: str, dry_run: bool = False) -> str | None:
        """Install Stitch skill files for this tool in the given project.

        Skills are tool-specific instruction sets that some AI tools
        auto-discover from specific directories (e.g., .cursor/skills/).
        Default: None (tool doesn't support skills).
        Override in subclasses that do.
        """
        return None

    def get_skill_paths(self) -> list[Path]:
        """Return paths where this tool looks for skill files.
        Default: empty (tool doesn't support skills).
        """
        return []

    def to_registry_dict(self) -> dict:
        """Backward-compat: export as dict matching the old TOOL_REGISTRY format."""
        return {"name": self.name}


class _PathDetectMixin:
    """Mixin for tools detected via path existence and/or command in PATH."""

    _detect_paths: list[Path] = []
    _detect_cmd: str | None = None

    def is_installed(self) -> tuple[bool, str]:
        for p in self._detect_paths:
            if p.exists():
                return True, f"found {p}"
        if self._detect_cmd and shutil.which(self._detect_cmd):
            return True, f"found `{self._detect_cmd}` in PATH"
        return False, ""


class _InstructionsMixin:
    """Mixin for tools that use global instruction file injection."""

    _instructions_file: Path | None = None

    def inject_instructions(self, dry_run: bool = False) -> str | None:
        if not self._instructions_file:
            return None
        if dry_run:
            return f"Would inject into {self._instructions_file}"
        self._instructions_file.parent.mkdir(parents=True, exist_ok=True)
        if _inject_into_file(self._instructions_file, GLOBAL_INSTRUCTIONS):
            return f"Injected into {self._instructions_file}"
        return f"Already present in {self._instructions_file}"


CURSOR_SKILL_CONTENT = """\
# Stitch — Agent Handoff & Context Protocol

Use this skill at the start of every session and when pushing context updates.

## Session Start
1. Run `python3 -m xstitch.cli auto-setup`
2. Run `python3 -m xstitch.cli auto "<user's first message>"`

## During Session
- After sub-tasks: `python3 -m xstitch.cli snap -m "what was done"`
- After decisions: `python3 -m xstitch.cli decide -p "problem" -c "chosen" -a "alts" -r "why"`
- Every 2-3 minutes: `python3 -m xstitch.cli snap -m "progress"`
- Before ending: `python3 -m xstitch.cli checkpoint -s "summary" -d "decisions" -e "experiments" -f "failures" -q "questions"`
"""


class _SkillsMixin:
    """Mixin for tools that support project-level skill files."""

    _skill_dir_name: str | None = None
    _skill_content: str = ""

    def inject_skills(self, project_path: str, dry_run: bool = False) -> str | None:
        if not self._skill_dir_name:
            return None
        skill_dir = Path(project_path) / self._skill_dir_name / "xstitch"
        skill_file = skill_dir / "SKILL.md"
        if dry_run:
            return f"Would create {skill_file}"
        skill_dir.mkdir(parents=True, exist_ok=True)
        if skill_file.exists():
            return f"Skill already exists at {skill_file}"
        skill_file.write_text(self._skill_content)
        return f"Created skill at {skill_file}"

    def get_skill_paths(self) -> list[Path]:
        if not self._skill_dir_name:
            return []
        return [Path(self._skill_dir_name) / "xstitch" / "SKILL.md"]


class JsonMcpTool(_PathDetectMixin, _InstructionsMixin, _SkillsMixin, ToolIntegration):
    """Tool that stores MCP config in a JSON file (Cursor, Windsurf, Zed, Gemini, Copilot)."""

    def __init__(self, name: str, *, detect_paths: list[Path], detect_cmd: str | None = None,
                 config_path: Path, mcp_key: str = "mcpServers",
                 extra_fields: dict | None = None, instructions_file: Path | None = None,
                 skill_dir: str | None = None):
        self.name = name
        self._detect_paths = detect_paths
        self._detect_cmd = detect_cmd
        self._config_path = config_path
        self._mcp_key = mcp_key
        self._extra_fields = extra_fields or {}
        self._instructions_file = instructions_file
        self._skill_dir_name = skill_dir
        self._skill_content = CURSOR_SKILL_CONTENT

    def inject_mcp(self, dry_run: bool = False) -> str | None:
        return _inject_json_mcp(self.name, self._config_path, self._mcp_key,
                                self._extra_fields, dry_run)

    def to_registry_dict(self) -> dict:
        d: dict = {"name": self.name, "mcp_config": self._config_path, "mcp_key": self._mcp_key}
        if self._detect_paths:
            d["detect_path"] = self._detect_paths[0]
        if self._detect_cmd:
            d["detect_cmd"] = self._detect_cmd
        if self._extra_fields:
            d["mcp_entry_extra"] = self._extra_fields
        if self._instructions_file:
            d["instructions_file"] = self._instructions_file
        return d


class ClaudeCodeTool(_PathDetectMixin, ToolIntegration):
    """Claude Code — MCP via direct config editing + CLI fallback."""

    name = "Claude Code"
    _detect_cmd = "claude"

    def inject_mcp(self, dry_run: bool = False) -> str | None:
        return _inject_claude_code_mcp(dry_run)

    def to_registry_dict(self) -> dict:
        return {"name": self.name, "detect_cmd": "claude", "mcp_via_cli": True}


class ContinueTool(_PathDetectMixin, ToolIntegration):
    """Continue.dev — MCP via standalone JSON file."""

    name = "Continue.dev"
    _detect_paths = [Path.home() / ".continue"]

    def __init__(self):
        self._mcp_file = Path.home() / ".continue" / "mcpServers" / "xstitch.json"

    def inject_mcp(self, dry_run: bool = False) -> str | None:
        return _inject_continue_mcp(self._mcp_file, dry_run)

    def to_registry_dict(self) -> dict:
        return {"name": self.name, "detect_path": self._detect_paths[0],
                "mcp_file": self._mcp_file}


class CodexTool(_PathDetectMixin, _InstructionsMixin, ToolIntegration):
    """Codex — MCP via TOML config + instruction file."""

    name = "Codex"
    _detect_paths = [Path.home() / ".codex"]
    _instructions_file = Path.home() / ".codex" / "AGENTS.md"

    def __init__(self):
        self._toml_path = Path.home() / ".codex" / "config.toml"

    def inject_mcp(self, dry_run: bool = False) -> str | None:
        return _inject_toml_mcp(self.name, self._toml_path, dry_run)

    def to_registry_dict(self) -> dict:
        return {"name": self.name, "detect_path": self._detect_paths[0],
                "mcp_via_toml": self._toml_path,
                "instructions_file": self._instructions_file}


class AiderTool(_PathDetectMixin, ToolIntegration):
    """Aider — instruction-file only (no MCP)."""

    name = "Aider"
    _detect_cmd = "aider"

    def __init__(self):
        self._config_path = Path.home() / ".aider.conf.yml"

    def inject_instructions(self, dry_run: bool = False) -> str | None:
        return _inject_aider_config(self._config_path, dry_run)

    def to_registry_dict(self) -> dict:
        return {"name": self.name, "detect_cmd": "aider",
                "instructions_file": self._config_path, "aider_mode": True}


ALL_TOOLS: list[ToolIntegration] = [
    JsonMcpTool("Cursor", detect_paths=[Path.home() / ".cursor"],
                config_path=Path.home() / ".cursor" / "mcp.json",
                skill_dir=".cursor/skills"),
    JsonMcpTool("Windsurf", detect_paths=[Path.home() / ".codeium" / "windsurf"],
                config_path=Path.home() / ".codeium" / "windsurf" / "mcp_config.json"),
    JsonMcpTool("Zed", detect_paths=[Path.home() / ".config" / "zed"],
                config_path=Path.home() / ".config" / "zed" / "settings.json",
                mcp_key="context_servers", extra_fields={"source": "custom"}),
    ContinueTool(),
    ClaudeCodeTool(),
    CodexTool(),
    JsonMcpTool("Gemini CLI", detect_paths=[Path.home() / ".gemini"],
                config_path=Path.home() / ".gemini" / "settings.json",
                instructions_file=Path.home() / ".gemini" / "GEMINI.md"),
    JsonMcpTool("Copilot CLI", detect_paths=[Path.home() / ".copilot"],
                detect_cmd="copilot",
                config_path=Path.home() / ".copilot" / "mcp-config.json"),
    AiderTool(),
]

# Backward compatibility: TOOL_REGISTRY is derived from the OOP objects.
TOOL_REGISTRY: list[dict] = [t.to_registry_dict() for t in ALL_TOOLS]

# Protocol injected into global instruction files (Codex AGENTS.md, Gemini GEMINI.md)
GLOBAL_INSTRUCTIONS = f"""\
{Stitch_SECTION_MARKER}
## Stitch — Agent Handoff & Context Protocol

This developer uses Stitch for cross-tool context preservation.
Stitch is installed. Use `python3 -m xstitch.cli` to run commands.
Stitch prevents duplicate notes automatically — push freely without worrying about repetition.

### Session Start (FIRST THING — before responding to the user)
```
python3 -m xstitch.cli auto-setup
python3 -m xstitch.cli auto "<user's first message>"
```

**Tell the user what happened:**
- Resumed: "I found existing context for '<title>' and loaded it."
- New task: "I created a new task '<title>' (ID: xxx)."
- No match: "No matching prior work found. Starting fresh."

### WHEN to Push — Specific Triggers

Push **immediately** when any of these happen:

1. **Completed a sub-task**: `python3 -m xstitch.cli snap -m "what was done + outcome"`
2. **Made a design/arch decision**: `python3 -m xstitch.cli decide -p "problem" -c "chosen" -a "alt1,alt2" -r "reasoning"`
3. **Experiment failed**: `python3 -m xstitch.cli snap -m "FAILED: what was tried + why it failed"`
4. **Hit a blocker**: `python3 -m xstitch.cli task update --blockers "description" --state "current state"`
5. **Every 2-3 minutes** of active work: `python3 -m xstitch.cli snap -m "progress summary"`

### Quality Rules
- Every snapshot must answer: **What** was done + **What** was the result.
- Every decision must answer: **What** problem + **What** chosen + **What** rejected + **Why**.
- Bad: "worked on auth" — Good: "Implemented JWT auth with RS256. Tokens expire in 1h."
- Prefix failed experiments with "FAILED:" so future agents avoid repeating them.

### Before Ending Your Session (or context summarization)
```
python3 -m xstitch.cli checkpoint -s "summary" -d "decisions" -e "experiments" -f "failures" -q "open questions"
python3 -m xstitch.cli task update --state "current state" --next "ordered next steps"
```

### Troubleshooting
- If `python3 -m xstitch.cli` fails with ModuleNotFoundError: run `pip3 install -e <path-to-xstitch>`
- If no tasks exist: run `python3 -m xstitch.cli task new "title"`
- To diagnose issues: run `python3 -m xstitch.cli doctor`
{Stitch_SECTION_MARKER}
"""


def _load_entry_point_tools() -> list[ToolIntegration]:
    """Discover third-party tool integrations registered via entry_points.

    Why entry_points over a config file:
      - Standard Python packaging mechanism (used by pytest, setuptools, etc.)
      - No Stitch-specific config format to learn
      - Works with pip install, editable installs, and venvs
      - Third-party packages simply add to pyproject.toml:
          [project.entry-points."xstitch.integrations"]
          my_tool = "my_plugin:MyTool"

    Silently ignores broken plugins to never block normal operation.
    """
    plugins: list[ToolIntegration] = []
    try:
        from importlib.metadata import entry_points
        eps = entry_points()
        stitch_eps = eps.select(group="xstitch.integrations") if hasattr(eps, "select") else eps.get("xstitch.integrations", [])
        builtin_names = {t.name for t in ALL_TOOLS}
        for ep in stitch_eps:
            if ep.name in builtin_names:
                continue
            try:
                cls = ep.load()
                tool = cls() if callable(cls) else cls
                if isinstance(tool, ToolIntegration):
                    plugins.append(tool)
            except Exception:
                pass
    except Exception:
        pass
    return plugins


def discover_all_tools() -> list[ToolIntegration]:
    """Return all known tools: built-in + entry_point plugins.

    This is the preferred API for getting the complete tool list.
    ALL_TOOLS contains only built-in tools; this function adds
    third-party plugins discovered via entry_points.
    """
    return list(ALL_TOOLS) + _load_entry_point_tools()


def detect_tools(quiet: bool = False) -> list[ToolIntegration]:
    """Detect which AI tools are installed on this machine."""
    found = []
    for tool in discover_all_tools():
        detected, method = tool.is_installed()
        if detected:
            found.append(tool)
            if not quiet:
                print(f"  [detected] {tool.name} ({method})")
    return found


def inject_mcp_for_tool(tool: ToolIntegration, dry_run: bool = False) -> str | None:
    """Inject Stitch MCP server config for a detected tool."""
    return tool.inject_mcp(dry_run)


def inject_instructions_for_tool(tool: ToolIntegration, dry_run: bool = False) -> str | None:
    """Inject Stitch session protocol for a detected tool."""
    return tool.inject_instructions(dry_run)


def generate_bootstrap(configured_tools: list[str]) -> Path:
    """Generate ~/.stitch/AGENT_BOOTSTRAP.md — the universal fallback for any agent."""
    GLOBAL_HOME.mkdir(parents=True, exist_ok=True)
    bootstrap_path = GLOBAL_HOME / "AGENT_BOOTSTRAP.md"

    tools_list = ", ".join(configured_tools) if configured_tools else "(none auto-configured yet)"

    cli = "python3 -m xstitch.cli"
    content = f"""\
# Stitch Agent Bootstrap

You have Stitch (Agent Handoff & Context Protocol) installed on this machine.
It preserves context (decisions, experiments, failures, state) across AI tool
switches so that you and future agents don't repeat work or mistakes.

**Already configured for**: {tools_list}

---

## At Session Start (run FIRST)

```bash
{cli} auto-setup                    # Idempotent project bootstrap
{cli} auto "<user's message>"       # Detects resume vs new, returns context
```

If the user wants to resume previous work, `{cli} auto` finds the right task
by relevance and returns a structured resume briefing with:
- WARNINGS about dead ends and failed experiments
- Architecture decisions with reasoning
- Exact next steps in order
- Live repo state verification

## WHEN to Push — Specific Triggers

Stitch prevents duplicates automatically — push freely without worrying about repetition.

Push **immediately** when any of these happen:

```bash
# 1. After completing any sub-task or step
{cli} snap -m "Implemented JWT auth with RS256 — tokens expire 1h with refresh rotation"

# 2. After making a design/architectural decision
{cli} decide -p "How to handle rate limiting" -c "Token bucket at gateway" -a "Per-service limiter, Client-side backoff" -r "Centralized is simpler"

# 3. After a failed experiment or dead end
{cli} snap -m "FAILED: Tried SQLite for caching — too slow under concurrent writes (>200ms p99)"

# 4. On blockers or direction changes
{cli} task update --blockers "Waiting on API key" --state "Auth module 80% done"

# 5. Periodically (every 3-5 meaningful actions)
{cli} snap -m "Progress: 4/7 endpoints migrated. Users, Auth, Products, Orders done."
```

## WHAT to Include

Every snapshot: **What** was done + **What** was the result. Be specific.
Every decision: **What** problem + **What** chosen + **What** rejected + **Why**.

## Before Session End or Context Summarization

```bash
{cli} checkpoint \\
    -s "summary of what was accomplished" \\
    -d "key decisions and reasoning" \\
    -e "experiments tried (pass and fail)" \\
    -f "dead ends and why they failed" \\
    -q "open questions and unresolved issues"
{cli} task update --state "exact current state" --next "ordered next steps with details"
```

## MCP Server (for tools that support Model Context Protocol)

If your tool supports MCP, register this server to get 14 native Stitch tools:

```json
{{
    "mcpServers": {{
        "xstitch": {{
            "command": "{PYTHON_BIN}",
            "args": ["-u", "-m", "xstitch.mcp_server"]
        }}
    }}
}}
```

Key MCP tools: `stitch_auto_route` (primary entry point), `stitch_snapshot`,
`stitch_add_decision`, `stitch_checkpoint`, `stitch_resume_briefing`,
`stitch_smart_match`, `stitch_create_task`, `stitch_update_task`.

## CLI Reference (all commands)

All commands: `{cli} <command>`

```
auto-setup          Idempotent project bootstrap
auto "<prompt>"     Intelligent routing (resume or new)
smart-match "<q>"   Relevance search across tasks
task new/list/show  Task management
snap -m "msg"       Snapshot with git state
decide -p/-c/-a/-r  Log decision with tradeoffs
checkpoint -s/-d/-e/-f/-q  Rich pre-summarization save
resume              Structured resume briefing
handoff             Token-budget-aware handoff bundle
inject              Inject into project-level config files
hooks install       Auto-snapshot on git commit
daemon start        Periodic background snapshots
launchd install     Reboot-safe daemon (macOS)
```

---
*Generated by Stitch v0.2.0. Python: `{PYTHON_BIN}`*
"""
    bootstrap_path.write_text(content)
    return bootstrap_path


def global_setup(dry_run: bool = False):
    """One-time machine setup: detect tools, configure MCP + instructions, generate bootstrap."""
    print("Stitch Global Setup")
    print("=" * 50)
    print()

    # 0. Health check
    from .healthcheck import quick_check
    health = quick_check()
    if health["status"] != "ok":
        print(f"  [WARNING] {health['reason']}")
        print(f"  [FIX] {health['fix']}")
        print()
    else:
        print(f"  [OK] Stitch installation healthy (python: {PYTHON_BIN})")
        print()

    # 1. Detect tools
    print("Scanning for installed AI tools...")
    tools = detect_tools()
    print()

    if not tools:
        print("No known AI tools detected.")
        print("Stitch will still work via CLI commands and the bootstrap file.")
        print()

    # 2. Configure each tool
    configured = []
    mcp_results = []
    instr_results = []

    for tool in tools:
        mcp_result = inject_mcp_for_tool(tool, dry_run=dry_run)
        if mcp_result:
            mcp_results.append((tool.name, mcp_result))

        instr_result = inject_instructions_for_tool(tool, dry_run=dry_run)
        if instr_result:
            instr_results.append((tool.name, instr_result))

        configured.append(tool.name)

    # 3. Print results
    if mcp_results:
        print("MCP Server Registration:")
        for name, result in mcp_results:
            print(f"  [{name}] {result}")
        print()

    if instr_results:
        print("Global Instructions Injection:")
        for name, result in instr_results:
            print(f"  [{name}] {result}")
        print()

    # 3b. Install Claude Code enforcement hooks (deterministic, cannot be skipped)
    from .enforcement import install_claude_code_hooks_global
    hooks_result = install_claude_code_hooks_global(dry_run=dry_run)
    if hooks_result:
        print("Enforcement Hooks:")
        print(f"  [Claude Code] {hooks_result}")
        print()

    # 4. Generate bootstrap
    if not dry_run:
        bootstrap_path = generate_bootstrap(configured)
        print(f"Universal bootstrap: {bootstrap_path}")
    else:
        print(f"Would generate bootstrap at {GLOBAL_HOME / 'AGENT_BOOTSTRAP.md'}")
    print()

    # 5. Summary
    print("=" * 50)
    if dry_run:
        print("DRY RUN — no changes were made.")
    else:
        print(f"Configured {len(configured)} tool(s): {', '.join(configured) or 'none'}")
        print()
        print("Next steps:")
        print("  1. Restart any open AI tools (Cursor, Windsurf, etc.) to load MCP")
        print("  2. In each project, run: python3 -m xstitch.cli auto-setup")
        print("  3. Run: stitch doctor — to verify everything is healthy")
        print("  4. For tools not listed above, tell the agent:")
        print(f"     'Read {GLOBAL_HOME / 'AGENT_BOOTSTRAP.md'} and follow its protocol'")


# --- Internal helpers ---

def _inject_json_mcp(
    tool_name: str,
    config_path: Path,
    mcp_key: str,
    extra_fields: dict,
    dry_run: bool,
) -> str:
    """Add Stitch to a JSON config file's MCP servers section."""
    if dry_run:
        return f"Would add to {config_path}"

    config_path.parent.mkdir(parents=True, exist_ok=True)

    config = {}
    if config_path.exists():
        try:
            raw = config_path.read_text().strip()
            if raw:
                config = json.loads(raw)
        except (json.JSONDecodeError, OSError) as e:
            return f"Skipped — config unreadable: {e}"

    servers = config.setdefault(mcp_key, {})

    entry = {**MCP_SERVER_ENTRY, **extra_fields}

    if "xstitch" in servers:
        if servers["xstitch"] == entry:
            return f"Already registered in {config_path}"
        servers["xstitch"] = entry
        config_path.write_text(json.dumps(config, indent=2) + "\n")
        return f"Updated stale config in {config_path}"

    servers["xstitch"] = entry
    config_path.write_text(json.dumps(config, indent=2) + "\n")
    return f"Added to {config_path}"


def _inject_claude_code_mcp(dry_run: bool) -> str:
    """Add/update Stitch MCP server in Claude Code's config.

    Claude Code stores per-project MCP configs in ~/.claude.json under
    projects[path].mcpServers. We update the global-level mcpServers AND
    fix any stale per-project entries (wrong python binary, missing -u flag).

    Falls back to `claude mcp add` CLI if direct config editing isn't possible.
    """
    if dry_run:
        return "Would update Stitch MCP in Claude Code config"

    correct_entry = {
        "type": "stdio",
        "command": PYTHON_BIN,
        "args": ["-u", "-m", "xstitch.mcp_server"],
        "env": {},
    }

    claude_config = Path.home() / ".claude.json"
    if claude_config.exists():
        try:
            data = json.loads(claude_config.read_text())
            updated = False

            # Fix global mcpServers
            global_servers = data.setdefault("mcpServers", {})
            if global_servers.get("xstitch") != correct_entry:
                global_servers["xstitch"] = correct_entry
                updated = True

            # Fix all per-project entries that have stale stitch configs
            for proj_path, proj_data in data.get("projects", {}).items():
                proj_servers = proj_data.get("mcpServers", {})
                if "xstitch" in proj_servers and proj_servers["xstitch"] != correct_entry:
                    proj_servers["xstitch"] = correct_entry
                    updated = True

            if updated:
                claude_config.write_text(json.dumps(data, indent=2) + "\n")
                return f"Updated MCP config in {claude_config}"
            return "Already registered in Claude Code"
        except (json.JSONDecodeError, OSError) as e:
            pass  # Fall through to CLI method

    # Fallback: use `claude mcp add` CLI
    try:
        cmd = [
            "claude", "mcp", "add",
            "--transport", "stdio",
            "xstitch", "--",
            PYTHON_BIN, "-u", "-m", "xstitch.mcp_server",
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        if result.returncode == 0:
            return "Registered via `claude mcp add`"
        return f"CLI returned exit {result.returncode}: {result.stderr[:100]}"
    except FileNotFoundError:
        return "Skipped — `claude` CLI not found"
    except subprocess.TimeoutExpired:
        return "Skipped — `claude mcp add` timed out"


def _inject_continue_mcp(mcp_file: Path, dry_run: bool) -> str:
    """Write a standalone MCP config file for Continue.dev."""
    if dry_run:
        return f"Would create {mcp_file}"

    if mcp_file.exists():
        try:
            data = json.loads(mcp_file.read_text())
            if data.get("mcpServers", {}).get("xstitch"):
                return f"Already registered in {mcp_file}"
        except (json.JSONDecodeError, OSError):
            pass

    mcp_file.parent.mkdir(parents=True, exist_ok=True)
    content = {"mcpServers": {"xstitch": MCP_SERVER_ENTRY}}
    mcp_file.write_text(json.dumps(content, indent=2) + "\n")
    return f"Created {mcp_file}"


def _inject_toml_mcp(tool_name: str, config_path: Path, dry_run: bool) -> str:
    """Add Stitch MCP server to a TOML config file (e.g. Codex config.toml).

    Codex uses TOML format:
        [mcp_servers.stitch]
        command = "/path/to/python3"
        args = ["-m", "xstitch.mcp_server"]

    We parse/write TOML manually (stdlib only — no tomllib write support)
    to avoid adding dependencies. The format is simple enough for direct
    string manipulation with safe checks.
    """
    if dry_run:
        return f"Would add MCP to {config_path}"

    config_path.parent.mkdir(parents=True, exist_ok=True)

    section_header = "[mcp_servers.stitch]"
    entry_lines = (
        f'{section_header}\n'
        f'command = "{PYTHON_BIN}"\n'
        f'args = ["-u", "-m", "xstitch.mcp_server"]\n'
        f'startup_timeout_sec = 30\n'
    )

    if config_path.exists():
        existing = config_path.read_text()
        if section_header in existing:
            has_correct_python = PYTHON_BIN in existing
            has_mcp_module = "xstitch.mcp_server" in existing
            has_unbuffered = '"-u"' in existing
            has_timeout = "startup_timeout_sec" in existing
            if has_correct_python and has_mcp_module and has_unbuffered and has_timeout:
                return f"Already registered in {config_path}"
            # Stale/incomplete entry — replace the section
            lines = existing.split("\n")
            new_lines = []
            skip = False
            for line in lines:
                if line.strip() == section_header:
                    skip = True
                    continue
                if skip and line.strip().startswith("["):
                    skip = False
                if skip and (line.strip().startswith("command") or
                             line.strip().startswith("args") or
                             line.strip().startswith("env") or
                             line.strip().startswith("startup_timeout") or
                             line.strip() == ""):
                    continue
                skip = False
                new_lines.append(line)
            content = "\n".join(new_lines).rstrip() + "\n\n" + entry_lines
            config_path.write_text(content)
            return f"Updated MCP config in {config_path}"

        content = existing.rstrip() + "\n\n" + entry_lines
        config_path.write_text(content)
        return f"Added MCP to {config_path}"

    config_path.write_text(entry_lines)
    return f"Created {config_path} with MCP"


def _inject_aider_config(config_path: Path, dry_run: bool) -> str:
    """Add Stitch conventions to Aider's config.

    Aider reads CONVENTIONS.md when listed in .aider.conf.yml.
    We add the read directive; the per-project CONVENTIONS.md is created by `python3 -m xstitch.cli inject`.
    """
    if dry_run:
        return f"Would update {config_path}"

    marker = "# Stitch"
    if config_path.exists():
        existing = config_path.read_text()
        if marker in existing:
            return f"Already configured in {config_path}"
        new_content = existing.rstrip() + f"\n\n{marker}\nread: [CONVENTIONS.md]\n"
    else:
        config_path.parent.mkdir(parents=True, exist_ok=True)
        new_content = f"{marker}\nread: [CONVENTIONS.md]\n"

    config_path.write_text(new_content)
    return f"Added CONVENTIONS.md read directive to {config_path}"
