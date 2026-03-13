"""Agent discovery module for Stitch.

Provides multiple mechanisms for AI agents to discover and load task context:

1. CLAUDE.md injection        — Claude Code reads this at session start
2. .cursorrules injection     — Cursor reads this for project rules
3. .github/copilot-instructions.md — GitHub Copilot reads this
4. AGENTS.md injection        — OpenAI Codex reads this at session start
5. GEMINI.md injection        — Google Gemini CLI reads this at session start
6. .windsurfrules injection   — Windsurf reads this for project rules
7. CONVENTIONS.md injection   — Aider reads this via .aider.conf.yml
8. PageIndex-like search      — structured index for semantic search
9. MCP integration            — native tool access (see mcp_server.py)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

Stitch_SECTION_MARKER = "<!-- Stitch-AUTO-INJECTED -->"

CLAUDE_MD_INJECTION = f"""\
{Stitch_SECTION_MARKER}
## Stitch — Context Preservation Protocol

### MANDATORY: Run at session start (before any response to the user)
```
python3 -m xstitch.cli auto-setup
python3 -m xstitch.cli auto "<user's first message>"
```
**Tell the user what happened:**
- Resumed: "I found and loaded saved context for '<title>'."
- New task: "I created a new task '<title>' (ID: xxx)."
- No match: "No prior context found. Starting fresh."

### WHEN to Push — Triggers (push immediately when any happen)

1. **Completed a sub-task**: `python3 -m xstitch.cli snap -m "what was done + outcome"`
2. **Made a decision**: `python3 -m xstitch.cli decide -p "problem" -c "chosen" -a "alternatives" -r "why"`
3. **Experiment failed**: `python3 -m xstitch.cli snap -m "FAILED: what was tried + why it failed"`
4. **Hit a blocker**: `python3 -m xstitch.cli task update --blockers "blocker" --state "current state"`
5. **Every 2-3 minutes** of active work: `python3 -m xstitch.cli snap -m "progress summary"`

### Quality Rules
- Every snapshot must answer: **What** was done + **What** was the result.
- Every decision must answer: **What** problem + **What** chosen + **What** rejected + **Why**.
- Bad: "worked on auth" — Good: "Implemented JWT auth with RS256. Tokens expire in 1h."
- Prefix failed experiments with "FAILED:" so future agents avoid repeating them.

### Before session end (or context summarization)
```
python3 -m xstitch.cli checkpoint -s "summary" -d "decisions" -e "experiments" -f "failures" -q "questions"
python3 -m xstitch.cli task update --state "current state" --next "ordered next steps"
```

### Troubleshooting
- If `python3 -m xstitch.cli` fails with ModuleNotFoundError: run `pip3 install -e <path-to-xstitch>`
- If no tasks exist: run `python3 -m xstitch.cli task new "title"`
- To diagnose issues: run `python3 -m xstitch.cli doctor`
{Stitch_SECTION_MARKER}
"""

CURSORRULES_INJECTION = f"""\
{Stitch_SECTION_MARKER}
# Stitch Context Protocol

## MANDATORY: Session start (before any response)
1. Call MCP tool `stitch_auto_route` with the user's message
2. Tell the user: "Loaded context for '<title>'" or "Created task '<title>'" or "No prior context found."

## WHEN to Push — Triggers (push immediately when any happen)
1. **Completed a sub-task**: `stitch_snapshot` — what was done + outcome
2. **Made a decision**: `stitch_add_decision` — problem + chosen + alternatives + reasoning
3. **Experiment failed**: `stitch_snapshot` — start message with "FAILED:" + what + why
4. **Hit a blocker**: `stitch_update_task` — set blockers and current state
5. **Every 2-3 minutes** of active work: `stitch_snapshot` — progress summary

## Quality: Be specific, not vague
- Bad: "worked on auth" — Good: "Implemented JWT auth with RS256. Tokens expire in 1h."
- Prefix failures with "FAILED:" so future agents avoid repeating them.

## Before session end
- `stitch_checkpoint` with summary/decisions/experiments/failures/questions
- `stitch_update_task` with current state and ordered next steps

## Troubleshooting
- If Stitch MCP tools are unavailable, use CLI: `python3 -m xstitch.cli auto-setup`
- To diagnose issues: `python3 -m xstitch.cli doctor`
{Stitch_SECTION_MARKER}
"""


INJECTION_TARGETS = [
    {"file": "CLAUDE.md",                      "agent": "Claude Code",     "content": "cli",  "tool_key": "Claude Code"},
    {"file": ".cursorrules",                    "agent": "Cursor",          "content": "mcp",  "tool_key": "Cursor"},
    {"file": ".cursor/rules/stitch-context.mdc",  "agent": "Cursor (rule)",   "content": "mdc",  "tool_key": "Cursor"},
    {"file": ".github/copilot-instructions.md", "agent": "GitHub Copilot",  "content": "mcp",  "tool_key": "GitHub Copilot"},
    {"file": "AGENTS.md",                       "agent": "OpenAI Codex",    "content": "cli",  "tool_key": "Codex"},
    {"file": "GEMINI.md",                       "agent": "Gemini CLI",      "content": "cli",  "tool_key": "Gemini CLI"},
    {"file": ".windsurfrules",                  "agent": "Windsurf",        "content": "mcp",  "tool_key": "Windsurf"},
    {"file": "CONVENTIONS.md",                  "agent": "Aider",           "content": "cli",  "tool_key": "Aider"},
]


def get_injected_file_paths() -> list[str]:
    """Return the list of ALL files Stitch could inject (single source of truth).

    Used by doctor checks, tests, and anywhere that needs the full list
    of files Stitch manages. Returns all possible files regardless of which
    tools are installed.
    """
    return [t["file"] for t in INJECTION_TARGETS]


def _get_installed_tool_names() -> set[str]:
    """Detect which AI tools are installed on this machine.

    Returns a set of tool names (matching TOOL_REGISTRY names in global_setup).
    Falls back to returning all tool keys if detection fails.
    """
    try:
        from .global_setup import detect_tools
        detected = detect_tools(quiet=True)
        return {t.name if hasattr(t, "name") else t["name"] for t in detected}
    except Exception:
        return {t["tool_key"] for t in INJECTION_TARGETS}


def inject_agent_discovery(project_path: str, force_all: bool = False):
    """Inject Stitch discovery instructions into agent config files.

    By default, only creates files for tools detected on this machine.
    Use force_all=True to inject into all files regardless.

    Gitignore always lists ALL possible files (safe for cross-machine repos).
    """
    project = Path(project_path)

    if force_all:
        installed = {t["tool_key"] for t in INJECTION_TARGETS}
    else:
        installed = _get_installed_tool_names()

    injected = []
    skipped = []

    for target in INJECTION_TARGETS:
        rel_path = target["file"]
        full_path = project / rel_path

        if target["tool_key"] not in installed:
            # Copilot: can't detect (it's an IDE extension), inject if .github/ exists
            if target["tool_key"] == "GitHub Copilot" and (project / ".github").exists():
                pass  # proceed with injection
            else:
                skipped.append((rel_path, target["agent"]))
                continue

        if target["content"] == "mdc":
            full_path.parent.mkdir(parents=True, exist_ok=True)
            full_path.write_text(
                "---\n"
                "description: \"Stitch context protocol — run stitch_auto_route at session start\"\n"
                "alwaysApply: true\n"
                "---\n\n"
                + CURSORRULES_INJECTION.replace(Stitch_SECTION_MARKER, "").strip()
                + "\n"
            )
            injected.append(rel_path)
        elif target["content"] == "mcp":
            full_path.parent.mkdir(parents=True, exist_ok=True)
            if _inject_into_file(full_path, CURSORRULES_INJECTION):
                injected.append(rel_path)
        elif target["content"] == "cli":
            if _inject_into_file(full_path, CLAUDE_MD_INJECTION):
                injected.append(rel_path)

    if injected:
        print(f"Injected Stitch discovery into: {', '.join(injected)}", file=sys.stderr)
    else:
        print("All agent config files already have Stitch injections.", file=sys.stderr)

    if skipped:
        names = ", ".join(f"{agent}" for _, agent in skipped)
        print(f"Skipped (not installed): {names}", file=sys.stderr)

    _update_gitignore(project)

    _generate_page_index(project)


def _inject_into_file(file_path: Path, content: str) -> bool:
    """Inject content into a file, replacing existing Stitch section if present."""
    if file_path.exists():
        existing = file_path.read_text()
        if Stitch_SECTION_MARKER in existing:
            parts = existing.split(Stitch_SECTION_MARKER)
            if len(parts) >= 3:
                # Properly paired markers — replace the section between them
                new_content = parts[0] + content + parts[-1]
                file_path.write_text(new_content)
                return True
            # Corrupted: odd number of markers. Strip all markers and re-inject.
            cleaned = existing.replace(Stitch_SECTION_MARKER, "").rstrip()
            file_path.write_text(cleaned + "\n\n" + content)
            return True
        file_path.write_text(existing + "\n\n" + content)
        return True
    else:
        file_path.write_text(content)
        return True


_GITIGNORE_MARKER = "# Stitch-AUTO-MANAGED"


def _update_gitignore(project: Path):
    """Add Stitch task data directory to .gitignore (idempotent).

    Only gitignores .stitch/ (legacy task data). Instruction files like
    CLAUDE.md, AGENTS.md, .cursorrules etc. are NOT gitignored because
    agents must be able to read them at session start.
    """
    gitignore = project / ".gitignore"
    entries = [".stitch/"]

    section = (
        f"{_GITIGNORE_MARKER}\n"
        + "\n".join(entries)
        + f"\n{_GITIGNORE_MARKER}\n"
    )

    if gitignore.exists():
        content = gitignore.read_text()
        if _GITIGNORE_MARKER in content:
            parts = content.split(_GITIGNORE_MARKER)
            if len(parts) >= 3:
                new_content = parts[0] + section + parts[-1]
                gitignore.write_text(new_content)
                return
            cleaned = content.replace(_GITIGNORE_MARKER, "").rstrip()
            gitignore.write_text(cleaned + "\n\n" + section)
            return
        gitignore.write_text(content.rstrip() + "\n\n" + section)
    else:
        gitignore.write_text(section)


def _generate_page_index(project: Path):
    """Generate a structured index of all tasks for PageIndex-like discovery."""
    from .store import PROJECTS_HOME, project_key
    key = project_key(project)
    data_dir = PROJECTS_HOME / key
    tasks_dir = data_dir / "tasks"
    if not tasks_dir.exists():
        return

    index_entries = []
    for task_dir in sorted(tasks_dir.iterdir()):
        meta_file = task_dir / "meta.json"
        if not meta_file.exists():
            continue

        meta = json.loads(meta_file.read_text())
        decisions = []
        dec_file = task_dir / "decisions.json"
        if dec_file.exists():
            dec_data = json.loads(dec_file.read_text())
            decisions = [d.get("problem", "") for d in dec_data]

        index_entries.append({
            "id": meta.get("id", ""),
            "title": meta.get("title", ""),
            "status": meta.get("status", ""),
            "objective": meta.get("objective", ""),
            "tags": meta.get("tags", []),
            "decisions": decisions,
            "path": str(task_dir),
            "updated_at": meta.get("updated_at", ""),
        })

    index_file = data_dir / "task_index.json"
    index_file.write_text(json.dumps(index_entries, indent=2))

    index_md = data_dir / "TASK_INDEX.md"
    lines = ["# Stitch Task Index", "", "| ID | Title | Status | Updated |", "|---|---|---|---|"]
    for e in index_entries:
        lines.append(
            f"| `{e['id']}` | {e['title']} | {e['status']} | {e['updated_at']} |"
        )
    lines.append("")
    for e in index_entries:
        lines.append(f"## {e['title']} (`{e['id']}`)")
        lines.append(f"- **Objective**: {e['objective']}")
        if e['tags']:
            lines.append(f"- **Tags**: {', '.join(e['tags'])}")
        if e['decisions']:
            lines.append(f"- **Decisions**: {'; '.join(e['decisions'][:5])}")
        lines.append(f"- **Context**: `{e['path']}/context.md`")
        lines.append("")

    index_md.write_text("\n".join(lines))
    print(f"Generated task index at .stitch/TASK_INDEX.md", file=sys.stderr)
