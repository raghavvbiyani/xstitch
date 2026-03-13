# Stitch Cross-Agent E2E Test — Step-by-Step Playbook

Test that Stitch preserves context when you switch between AI tools.
Tasks are intentionally simple and each one depends on the previous.

**Scenario**: Build a Python TODO app, one piece at a time, each in a different AI tool.

---

## Step 0 — One-Time Setup (5 minutes)

Run these commands once. They create a fresh test project and seed it with Stitch.

```bash
# 1. Create an isolated test project
mkdir -p /tmp/stitch-e2e-test && cd /tmp/stitch-e2e-test
git init
echo "# TODO App" > README.md
echo "*.pyc" > .gitignore
touch todo.py
git add -A && git commit -m "initial skeleton"

# 2. Initialize Stitch in this project
python3 -m xstitch.cli auto-setup

# 3. Create the starting task
python3 -m xstitch.cli task new "Build a Python TODO app" \
    -o "Build a simple command-line TODO app in todo.py, step by step across different AI tools"

# 4. Seed some initial context (simulates a prior planning session)
python3 -m xstitch.cli decide \
    -p "What language and structure to use" \
    -c "Single-file Python (todo.py) with a TodoApp class" \
    -a "Multi-file, Flask web app, Django" \
    -r "Keep it simple — single file is easiest for testing cross-agent handoff"

python3 -m xstitch.cli snap -m "Project initialized. todo.py is empty. Plan: build incrementally — add, list, delete, persist, then tests."

python3 -m xstitch.cli task update \
    --state "Empty todo.py created. Architecture decided: single-file TodoApp class." \
    --next "1. Implement TodoApp class with add() and list() methods. 2. Store todos as list of dicts with id, text, done fields."

git add -A && git commit -m "Stitch initialized with task and plan"
```

**Verify setup worked:**

```bash
python3 -m xstitch.cli task show
# Should show: "Build a Python TODO app" with objective, state, and next steps
python3 -m xstitch.cli resume
# Should show: resume briefing with the decision and snapshot
```

---

## Phase 1 — Cursor (or any first tool)

**What this tests**: Agent discovers Stitch, reads saved decisions, and pushes its own work back.

### Open the project

Open `/tmp/stitch-e2e-test` in **Cursor**.

### Paste this prompt

```
Implement the TodoApp class in todo.py with add() and list_todos() methods.
Each todo should have an id, text, and done status.
Add a simple if __name__ == "__main__" block that demos add and list.
```

### What the agent should do automatically

1. Discover Stitch (via `.cursorrules` or MCP tools)
2. Find the existing task "Build a Python TODO app"
3. See the decision: "Single-file Python with TodoApp class"
4. See next steps: "Implement add() and list() methods"
5. Implement the code
6. Push snapshots/decisions back to Stitch

### Verify after this phase

```bash
cd /tmp/stitch-e2e-test

# 1. Code exists and runs
python3 todo.py
# Should print some demo output

# 2. Stitch context was updated
python3 -m xstitch.cli task show
# Should show updated state/snapshots from the agent

python3 -m xstitch.cli resume
# Should show Phase 1 activity in session history

# 3. Commit the work (if agent didn't already)
git add -A && git commit -m "Phase 1: add and list" 2>/dev/null || true
```

---

## Phase 2 — Claude Code (or any second tool)

**What this tests**: A different agent picks up the EXACT state left by Phase 1 — knows what was built, what decisions were made, and continues without redoing anything.

### Open the project

```bash
cd /tmp/stitch-e2e-test
# Open Claude Code CLI, or open this folder in a different IDE/tool
```

### Paste this prompt

```
Resume the TODO app. Add delete_todo() and mark_done() methods to the
TodoApp class. The delete should work by todo id. Update the main block
to demo all four operations: add, list, mark done, delete.
```

### What the agent should do automatically

1. Discover Stitch (via `CLAUDE.md` or MCP)
2. Find and resume the existing task
3. See Phase 1's code structure (TodoApp class with add/list)
4. See the architecture decision (single-file, TodoApp class)
5. Add delete and mark_done WITHOUT restructuring what Phase 1 built
6. Push its own snapshots/decisions back

### Verify after this phase

```bash
cd /tmp/stitch-e2e-test

# 1. All four operations work
python3 todo.py

# 2. Stitch has BOTH Phase 1 and Phase 2 history
python3 -m xstitch.cli resume
# Session History should show entries from BOTH phases

# 3. Commit
git add -A && git commit -m "Phase 2: delete and mark_done" 2>/dev/null || true
```

---

## Phase 3 — Codex / Antigravity / Gemini (or any third tool)

**What this tests**: The third agent sees the FULL history from Phases 1 and 2, including which methods exist and what architecture was chosen.

### Open the project

```bash
cd /tmp/stitch-e2e-test
# Open in Codex, Antigravity (Gemini CLI), or any other AI tool
```

### Paste this prompt

```
Resume the TODO app. Add file persistence — todos should be saved to a
todos.json file so they survive restarts. Load on startup, save after
every change. Keep backward compatible with the existing TodoApp class.
```

### What the agent should do automatically

1. Discover Stitch
2. Resume the task — see add, list, delete, mark_done from Phases 1+2
3. Know the TodoApp class structure (from context)
4. Add JSON file persistence without breaking existing methods
5. Push decisions (e.g., "chose JSON over SQLite for simplicity")

### Verify after this phase

```bash
cd /tmp/stitch-e2e-test

# 1. Persistence works
python3 -c "
from todo import TodoApp
app = TodoApp()
app.add('test persistence')
"
# Check todos.json exists
cat todos.json

python3 -c "
from todo import TodoApp
app = TodoApp()
print(app.list_todos())  # Should show 'test persistence'
"

# 2. Stitch has full 3-phase history
python3 -m xstitch.cli resume
# Should show warnings, decisions, and snapshots from ALL 3 phases

# 3. Clean up test data and commit
rm -f todos.json
git add -A && git commit -m "Phase 3: file persistence" 2>/dev/null || true
```

---

## Phase 4 — Any fourth tool (or same tool, different model)

**What this tests**: The agent sees the COMPLETE history — all 3 prior phases, all decisions, all code changes — and can write meaningful tests and documentation.

### Open the project

```bash
cd /tmp/stitch-e2e-test
# Open in any AI tool you haven't used yet, or switch models in Cursor
```

### Paste this prompt

```
Resume the TODO app. Write tests in test_todo.py covering add, list,
delete, mark_done, and file persistence. Then update README.md with
usage docs. Finally mark the task as completed.
```

### What the agent should do automatically

1. Discover Stitch
2. Resume — see FULL context from all 3 prior phases
3. Know all 5 features: add, list, delete, mark_done, persistence
4. Write tests that cover all features (using the actual API)
5. Write README referencing the architecture decisions
6. Mark the Stitch task as "completed"

### Verify after this phase (final)

```bash
cd /tmp/stitch-e2e-test

# 1. Tests pass
python3 -m pytest test_todo.py -v 2>/dev/null || python3 -m unittest test_todo -v

# 2. README has content
cat README.md

# 3. Task is marked completed
python3 -m xstitch.cli task show
# Status should be: completed

# 4. Full history preserved
python3 -m xstitch.cli resume
# Should show the COMPLETE journey across all 4 phases:
#   - Architecture decision (Phase 0)
#   - Implementation snapshots (Phase 1)
#   - Delete/mark_done additions (Phase 2)
#   - Persistence decision (Phase 3)
#   - Tests and docs (Phase 4)

# 5. Commit
git add -A && git commit -m "Phase 4: tests, docs, completed" 2>/dev/null || true
```

---

## What Proves Stitch Works

| Check | What it means |
|-------|--------------|
| Phase 2 agent didn't re-implement add/list | It read Phase 1 context |
| Phase 3 agent kept TodoApp class structure | It read the architecture decision |
| Phase 3 agent didn't use SQLite | It saw "JSON for simplicity" in prior decisions |
| Phase 4 agent tested ALL 5 features | It knew the full feature set from 3 prior phases |
| Phase 4 agent marked task completed | It used Stitch to update task status |
| `python3 -m xstitch.cli resume` shows all phases | Context was preserved and accumulated |

---

## If an Agent Doesn't Discover Stitch

Some agents might not automatically read project config files. Quick fixes:

**Option A — Nudge it** (add to your prompt):
```
Check this project's rules and conventions files before starting.
```

**Option B — Tell it directly** (add to your prompt):
```
This project uses Stitch for context. Run: python3 -m xstitch.cli auto "your task description"
```

**Option C — Run Stitch manually** between phases:
```bash
# Before starting a new phase, run:
python3 -m xstitch.cli resume
# Copy the output and paste it into the agent's prompt as context
```

---

## Cleanup

```bash
rm -rf /tmp/stitch-e2e-test
```
