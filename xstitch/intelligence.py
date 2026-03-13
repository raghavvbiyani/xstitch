"""Intelligence layer for Stitch.

Handles:
1. Auto-setup: Idempotent one-shot project bootstrap (init + inject + hooks)
2. Smart-match: BM25 relevance-based task discovery (not keyword similarity)
3. Intent detection: Determine if the user wants to resume or start fresh
4. Auto-routing: Given a prompt, return the right context or create a new task
5. Resume briefing: Structured context to prevent new agent from producing garbage

Uses the PageIndex-inspired principle: RELEVANCE ≠ SIMILARITY.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Optional

from .store import Store
from .models import Task, from_json
from .capture import is_git_repo
from .relevance import (
    BM25RelevanceEngine,
    generate_resume_briefing,
    scan_workspace_for_context,
    _tokenize,
)

def _get_workspace_root(project_path: str) -> str:
    """Derive workspace root from env var or project's parent directory."""
    env = os.environ.get("Stitch_WORKSPACE_ROOT")
    if env and os.path.isdir(env):
        return env
    return str(Path(project_path).resolve().parent)

RESUME_SIGNALS = {
    "resume", "continue", "pick up", "carry on", "where we left",
    "where i left", "left off", "back to", "get back", "previous",
    "earlier", "last time", "ongoing", "in progress", "existing",
    "same task", "that task", "the task", "unfinished", "pending",
    "follow up", "followup", "follow-up",
}

NEW_SIGNALS = {
    "new task", "start fresh", "from scratch", "brand new", "create",
    "begin", "initialize", "setup", "set up", "build a new",
    "build a ", "implement a ", "write a ", "add a ", "make a ",
    "develop a ", "design a ",
}

# Short greetings and conversational prompts that are NOT task-related.
# These should never trigger loading a full task context or creating a new task.
_GREETING_PATTERNS = {
    "hi", "hello", "hey", "yo", "sup", "howdy", "greetings",
    "good morning", "good afternoon", "good evening", "good night",
    "what's up", "whats up", "how are you", "how r u",
    "thanks", "thank you", "thx", "bye", "goodbye", "see you",
    "ok", "okay", "sure", "yes", "no", "yeah", "nah", "yep", "nope",
}


def _is_conversational(prompt: str) -> bool:
    """Detect if a prompt is a short greeting or conversational filler.

    These prompts have no task intent — loading a full task briefing for
    "hi claude" wastes tokens and confuses the agent into thinking
    it should resume work the user didn't ask about.
    """
    cleaned = re.sub(r'[^a-z0-9\s]', '', prompt.lower()).strip()
    # Remove common agent name suffixes
    for name in ("claude", "cursor", "codex", "copilot", "gemini", "aider"):
        cleaned = cleaned.replace(name, "").strip()
    if not cleaned:
        return True
    if cleaned in _GREETING_PATTERNS:
        return True
    # Very short prompts (< 4 words) with no actionable content
    words = cleaned.split()
    if len(words) <= 3 and all(w in _GREETING_PATTERNS for w in words):
        return True
    return False


def auto_setup(project_path: str | None = None, quiet: bool = False) -> dict:
    """Idempotent project bootstrap. Safe to call every session."""
    from . import log
    from .healthcheck import quick_check

    if not quiet:
        log.status("SETUP", "Running health check...")

    health = quick_check()
    if health["status"] != "ok":
        if not quiet:
            log.warn(health["reason"], fix=health.get("fix", ""))
            log.troubleshoot(
                "Stitch cannot operate correctly with a broken installation",
                health.get("fix", "Run: pip3 install -e <path-to-xstitch>"),
            )
    elif not quiet:
        log.ok("Installation healthy")

    store = Store(project_path)
    result = {"already_setup": False, "actions": [], "health": health}

    stitch_dir = store.local_dir
    if stitch_dir.exists() and (stitch_dir / "tasks").exists():
        result["already_setup"] = True
        if not quiet:
            log.ok(f"Project initialized ({store.project_key} at {stitch_dir})")
    else:
        store.init_project()
        result["actions"].append("initialized project storage")
        if not quiet:
            log.ok(f"Initialized project storage at {stitch_dir}")

    from .discovery import (
        inject_agent_discovery, Stitch_SECTION_MARKER, _update_gitignore,
        INJECTION_TARGETS, _get_installed_tool_names,
    )
    installed = _get_installed_tool_names()
    project = Path(store.project_path)
    needs_inject = False
    for target in INJECTION_TARGETS:
        if target["tool_key"] not in installed:
            continue
        f = project / target["file"]
        if target["content"] == "mdc":
            if not f.exists():
                needs_inject = True
                break
        else:
            if not f.exists() or Stitch_SECTION_MARKER not in f.read_text():
                needs_inject = True
                break

    if needs_inject:
        inject_agent_discovery(str(store.project_path))
        result["actions"].append("injected agent discovery")
        if not quiet:
            log.ok("Injected Stitch instructions into agent config files")
    else:
        _update_gitignore(project)

    if is_git_repo(str(store.project_path)):
        from .hooks import install_hooks, HOOK_MARKER
        git_dir = Path(store.project_path) / ".git" / "hooks" / "post-commit"
        if not git_dir.exists() or HOOK_MARKER not in git_dir.read_text():
            install_hooks(str(store.project_path))
            result["actions"].append("installed git hooks")
            if not quiet:
                log.ok("Installed git post-commit hook")

    active = store.get_active_task_id()
    result["active_task_id"] = active
    if active:
        task = store.get_task(active)
        result["active_task_title"] = task.title if task else None
        if not quiet:
            log.status("ACTIVE TASK", f"{active} — {result.get('active_task_title', '?')}")
    elif not quiet:
        log.info("No active task. Create one with: stitch task new \"title\"")

    if not quiet and not result["actions"] and result["already_setup"]:
        log.ok("Setup complete — already configured")

    return result


def smart_match(query: str, store: Store, top_k: int = 5) -> list[dict]:
    """Find tasks relevant to a query using BM25 scoring.

    This is RELEVANCE-based (PageIndex-inspired), not similarity-based:
    - Rare terms score higher (BM25 IDF: "postgresql" appearing in 1 task
      is a stronger signal than "api" appearing in all tasks)
    - Hierarchical field weighting (title > decisions > snapshots)
    - Cross-field matching (a query about "database migration" finds a task
      whose decisions mention "Alembic migration tool")
    """
    engine = BM25RelevanceEngine()
    engine.index(store)
    return engine.search(query, top_k=top_k)


def detect_intent(user_prompt: str) -> str:
    """Detect whether the user wants to resume an existing task or start new.

    Returns: 'resume', 'new', or 'ambiguous'
    """
    prompt_lower = user_prompt.lower()

    resume_score = 0
    new_score = 0

    for signal in RESUME_SIGNALS:
        if signal in prompt_lower:
            resume_score += 1

    for signal in NEW_SIGNALS:
        if signal in prompt_lower:
            new_score += 1

    # Task ID pattern (hex string)
    if re.search(r'\b[a-f0-9]{8,12}\b', prompt_lower):
        resume_score += 3

    if resume_score > new_score and resume_score > 0:
        return "resume"
    elif new_score > resume_score and new_score > 0:
        return "new"
    return "ambiguous"


def auto_route(user_prompt: str, store: Store) -> dict:
    """The main intelligence entry point.

    Given a user prompt:
    1. Auto-sets up the project
    2. Detects intent (resume / new / ambiguous)
    3. For resume: BM25 relevance search + workspace scan + resume briefing.
       If the user explicitly says "resume" but BM25 finds nothing, the
       active task is a reasonable default (the user asked to continue).
    4. For new: creates a task
    5. For ambiguous: BM25 relevance search is the ONLY gate for loading
       context. If nothing matches, the active task is mentioned but its
       full briefing is NOT injected.

    DESIGN PRINCIPLE: Context is loaded ONLY when there is a relevance
    match between the user's prompt and persisted task data. We never
    force-inject context for an unrelated prompt — whether that prompt
    is "hi claude" or "fix the login bug" while the active task is about
    database migration. The active task pointer is informational, not a
    trigger for context loading.
    """
    auto_setup(str(store.project_path), quiet=True)

    intent = detect_intent(user_prompt)
    result = {
        "intent": intent, "action": None, "task": None,
        "context": "", "briefing": "", "matches": [],
        "workspace_hints": [],
    }

    if intent == "resume":
        # User explicitly said "resume"/"continue" — search for a match,
        # and fall back to the active task (since they asked to resume,
        # the active task is a reasonable default).
        result = _handle_resume(user_prompt, store, result)

    elif intent == "new":
        title = _extract_task_title(user_prompt)
        task = store.create_task(title=title, objective=user_prompt[:500])
        result["action"] = "created"
        result["task"] = task

    else:  # ambiguous — relevance is the ONLY gate
        matches = smart_match(user_prompt, store)
        if matches and matches[0]["confidence"] >= 0.4:
            # Strong relevance match found — load that task's context
            result = _handle_resume(user_prompt, store, result)
        else:
            # No relevance match. Mention active task if one exists,
            # but do NOT load its full briefing.
            active_id = store.get_active_task_id()
            if active_id:
                task = store.get_task(active_id)
                if task:
                    result["action"] = "active_task_exists"
                    result["task"] = task
                    return result

            # No active task, no match. Only create a new task if
            # the prompt has enough substance to be actionable work.
            if _is_conversational(user_prompt):
                result["action"] = "greeting"
            else:
                title = _extract_task_title(user_prompt)
                task = store.create_task(title=title, objective=user_prompt[:500])
                result["action"] = "created"
                result["task"] = task

    return result


def _handle_resume(user_prompt: str, store: Store, result: dict) -> dict:
    """Handle a resume intent: search, verify, brief."""
    matches = smart_match(user_prompt, store)

    query_tokens = _tokenize(user_prompt)
    workspace_hints = []
    ws_root = _get_workspace_root(str(store.project_path))
    if os.path.isdir(ws_root):
        workspace_hints = scan_workspace_for_context(ws_root, query_tokens)
    result["workspace_hints"] = workspace_hints

    if matches and matches[0]["confidence"] >= 0.4:
        best = matches[0]
        task = best["task"]

        # Validate the task is accessible from the current project
        if store.task_is_local(task.id):
            store.switch_task(task.id)
            briefing = generate_resume_briefing(task.id, store)
            result["action"] = "resumed"
            result["task"] = task
            result["briefing"] = briefing
            result["confidence"] = best["confidence"]
            result["evidence"] = best["evidence"]
            result["matches"] = matches[:5]
        else:
            # Task exists in a different project
            other_project = task.project_path or store.get_task_project_path(task.id)
            result["action"] = "found_in_other_project"
            result["task"] = task
            result["confidence"] = best["confidence"]
            result["evidence"] = best["evidence"]
            result["other_project"] = other_project
            result["matches"] = matches[:5]

    elif matches:
        result["action"] = "show_matches"
        result["matches"] = matches[:5]

    else:
        active_id = store.get_active_task_id()
        if active_id:
            task = store.get_task(active_id)
            if task:
                briefing = generate_resume_briefing(active_id, store)
                result["action"] = "loaded_active"
                result["task"] = task
                result["briefing"] = briefing
        else:
            result["action"] = "no_match"

    return result


def _clean_evidence(evidence: list[str]) -> str:
    """Convert raw BM25 evidence into readable matched-terms summary."""
    terms = set()
    for e in evidence:
        # e looks like "title:rate(idf=1.3)" — extract the term
        if ":" in e and "(" in e:
            term = e.split(":")[1].split("(")[0]
            terms.add(term)
    return ", ".join(sorted(terms)) if terms else ""


def format_auto_route_response(result: dict) -> str:
    """Format the auto_route result as an agent-readable response.

    The output includes [TELL USER] markers — the agent MUST relay these
    lines to the user so they know context is being preserved.

    The output includes [Stitch ...] status lines so agents and users can see
    exactly what Stitch is doing at each step.
    """
    lines = []

    lines.append(f"  [Stitch ROUTE] Intent detected: {result.get('intent', '?')}")

    if result["action"] == "resumed":
        task = result["task"]
        conf = result.get("confidence", 0)
        evidence = result.get("evidence", [])
        matched = _clean_evidence(evidence)
        lines.append(f"  [Stitch OK] Found matching task: `{task.id}` — {task.title}")
        lines.append(f"  [Stitch OK] Confidence: {conf:.0%}")
        if matched:
            lines.append(f"  [Stitch OK] Matched terms: {matched}")
        lines.append(f"  [Stitch OK] Loaded saved context — resuming from last session")
        lines.append("")
        lines.append(f"[TELL USER]: \"I found saved context for '{task.title}' and loaded it. Picking up from the last session.\"")
        lines.append("")
        lines.append(result["briefing"])

    elif result["action"] == "created":
        task = result["task"]
        lines.append(f"  [Stitch OK] Created new task: `{task.id}` — {task.title}")
        lines.append(f"  [Stitch INFO] No prior context found. Starting fresh.")
        lines.append(f"  [Stitch INFO] Context will be saved automatically for future sessions.")
        lines.append("")
        lines.append(f"[TELL USER]: \"I created a new task '{task.title}' (ID: {task.id[:8]}) to track this work. Context will be saved for future sessions.\"")

    elif result["action"] == "show_matches":
        lines.append(f"  [Stitch INFO] Found {len(result['matches'])} possible matching tasks")
        lines.append("")
        for i, m in enumerate(result["matches"], 1):
            t = m["task"]
            conf = m.get("confidence", 0)
            lines.append(f"  {i}. `{t.id}` — {t.title} ({conf:.0%})")
            lines.append(f"     Project: {t.project_path}")
            if t.current_state:
                lines.append(f"     State: {t.current_state[:100]}")
            lines.append("")
        lines.append("[TELL USER]: \"I found multiple tasks that might match. Which one should I resume?\" Then list the options.")

    elif result["action"] == "loaded_active":
        task = result["task"]
        lines.append(f"  [Stitch OK] Loaded active task: `{task.id}` — {task.title}")
        lines.append(f"  [Stitch OK] Loaded saved context from previous sessions")
        lines.append("")
        lines.append(f"[TELL USER]: \"I loaded the active task '{task.title}' with saved context from previous sessions.\"")
        lines.append("")
        lines.append(result["briefing"])

    elif result["action"] == "found_in_other_project":
        task = result["task"]
        conf = result.get("confidence", 0)
        other = result.get("other_project", "unknown")
        lines.append(f"  [Stitch WARNING] Found matching task `{task.id}` — {task.title}")
        lines.append(f"  [Stitch WARNING] But it lives in a different project: {other}")
        lines.append("")
        lines.append(f"[TELL USER]: \"I found a matching task '{task.title}' but it's in a different project ({other}). Would you like me to create a new task here, or should you switch to that project?\"")

    elif result["action"] == "active_task_exists":
        task = result["task"]
        lines.append(f"  [Stitch INFO] Active task exists: `{task.id}` — {task.title}")
        lines.append(f"  [Stitch INFO] Prompt does not appear to be related to this task.")
        lines.append(f"  [Stitch INFO] NOT loading full context — ask the user what they need.")
        lines.append("")
        lines.append(f"[TELL USER]: \"There's an active Stitch task '{task.title}'. If you want to resume it, just say so. Otherwise I can help with whatever you need.\"")

    elif result["action"] == "greeting":
        lines.append("  [Stitch INFO] Conversational prompt — no task action needed.")
        lines.append("")

    elif result["action"] == "no_match":
        lines.append("  [Stitch INFO] No matching tasks found and no active task in this project.")
        lines.append("  [Stitch INFO] Will start fresh — create a task for this work.")
        lines.append("")
        lines.append("[TELL USER]: \"No prior context found for this work. Starting fresh.\" Then create a new task if the user's request is clear.")

    # Workspace hints
    hints = result.get("workspace_hints", [])
    if hints and result["action"] in ("show_matches", "no_match"):
        lines.append("")
        lines.append("Related projects in workspace:")
        for h in hints:
            lines.append(f"  - `{h['project_name']}` ({', '.join(h['evidence'][:3])})")

    return "\n".join(lines)


# --- Helpers ---

def _extract_task_title(prompt: str) -> str:
    sentences = re.split(r'[.!?\n]', prompt)
    title = sentences[0].strip() if sentences else prompt[:80]
    for prefix in ["i want to ", "please ", "can you ", "let's ", "lets ",
                    "help me ", "i need to ", "we need to "]:
        if title.lower().startswith(prefix):
            title = title[len(prefix):]
    title = title.strip().capitalize()
    if len(title) > 100:
        title = title[:97] + "..."
    return title or "Untitled task"
