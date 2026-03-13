# Stitch Setup Instructions

You are an AI coding agent. Follow these instructions to install and configure
Stitch (Agent Handoff & Context Protocol) on this developer's machine.

Stitch preserves your context (decisions, experiments, failures, state) across
AI tool switches so you and future agents don't repeat work or mistakes.

Execute each step. If a step fails, diagnose and fix before proceeding.

---

## Step 1: Check if Stitch is already installed

```bash
python3 -m xstitch.cli --help
```

If this prints the Stitch help text, skip to Step 3.
If it fails with "No module named xstitch", continue to Step 2.

## Step 2: Install Stitch

Stitch is a Python package with zero external dependencies.

```bash
# Option A: From a local clone
pip3 install -e /path/to/AgentHandOffAndContextProtocol

# Option B: From GitHub (replace with actual URL when published)
# pip3 install git+https://github.com/<owner>/AgentHandOffAndContextProtocol.git
```

If `pip3` is not available, try `pip` or `python3 -m pip install -e ...`.

Verify: `python3 -m xstitch.cli --help` should list all commands.

## Step 3: Global setup (one-time, configures ALL AI tools on this machine)

```bash
python3 -m xstitch.cli global-setup
```

This detects installed AI tools and auto-configures each one:
- **MCP-capable tools** (Cursor, Claude Code, Windsurf, Zed, Continue.dev):
  Registers `xstitch.mcp_server` so you get 14 native Stitch tools in every session.
- **Instruction-file tools** (Codex, Gemini CLI, Aider):
  Adds the Stitch session protocol to global instruction files.
- **All tools**: Generates `~/.stitch/AGENT_BOOTSTRAP.md` as a universal fallback.

After running, **restart any open AI tools** (Cursor, Windsurf, etc.) to load
the new MCP configuration.

## Step 4: Per-project setup

Navigate to the project directory and run:

```bash
cd /path/to/project
python3 -m xstitch.cli auto-setup
```

This is idempotent (safe to run repeatedly). It will:
1. Create `.stitch/` directory structure
2. Inject the Stitch protocol into all project-level config files:
   `CLAUDE.md`, `.cursorrules`, `AGENTS.md`, `GEMINI.md`, `.windsurfrules`,
   `CONVENTIONS.md`, `.github/copilot-instructions.md`
3. Install git hooks for automatic snapshots on every commit

## Step 5: Create or resume a task

If starting fresh:

```bash
python3 -m xstitch.cli task new "Short task title" -o "What you want to achieve" -t "tag1,tag2"
```

If resuming previous work:

```bash
python3 -m xstitch.cli auto "resume the authentication work"
```

This uses BM25 relevance scoring to find the right task and returns a
structured resume briefing with warnings about past failures, architecture
decisions, and exact next steps.

## Step 6: (Optional) Reboot-safe daemon

For automatic periodic snapshots that survive machine restarts (macOS):

```bash
python3 -m xstitch.cli launchd install --interval 600
```

---

## For Agents That Were Not Auto-Configured

If `python3 -m xstitch.cli global-setup` did not detect your tool (it's new or
unknown), you have two options:

### Option A: Register the MCP server manually

If your tool supports MCP (Model Context Protocol), add this to its config:

```json
{
    "mcpServers": {
        "xstitch": {
            "command": "python3",
            "args": ["-m", "xstitch.mcp_server"]
        }
    }
}
```

### Option B: Read the bootstrap file

If your tool reads instruction files, tell it:

> Read `~/.stitch/AGENT_BOOTSTRAP.md` and follow its protocol.

That file contains everything needed to use Stitch via CLI commands.

### Option C: Follow this protocol manually

**At session start:**
```bash
python3 -m xstitch.cli auto-setup
python3 -m xstitch.cli auto "<user's message>"
```

**During work:**
```bash
python3 -m xstitch.cli snap -m "what was done and the result"
python3 -m xstitch.cli decide -p "problem" -c "chosen" -a "alt1,alt2" -r "reasoning"
```

**Before session end:**
```bash
python3 -m xstitch.cli checkpoint -s "summary" -d "decisions" -e "experiments" -f "failures"
python3 -m xstitch.cli task update --state "current state" --next "next steps"
```

---

## How It Works After Setup

Once set up, everything is automatic:

| What | How | Automatic? |
|------|-----|------------|
| Snapshots on git commit | Git hooks (installed by auto-setup) | Yes |
| Agent discovers Stitch | MCP tools or injected instruction files | Yes |
| Context loaded on resume | Agent calls `python3 -m xstitch.cli auto` at session start | Yes |
| Decisions/snapshots pushed | Agent instructed to push proactively | Yes |
| Checkpoint before summarization | Agent instructed in config files | Yes |

The developer does not need to mention Stitch, type special keywords, or do
anything manual. The agent knows about Stitch because its MCP tools include it
or its instruction files tell it to use Stitch.

---

## Verification

After setup, verify everything works:

```bash
python3 -m xstitch.cli global-setup --dry-run    # Shows what tools are configured
python3 -m xstitch.cli auto-setup                # Should say "already configured"
python3 -m xstitch.cli task list                 # Should show tasks (if any exist)
python3 -m xstitch.cli --help                    # Should list all commands
```
