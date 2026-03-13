# Adding AI Tool Integrations to Stitch

This guide explains how to add new AI tool integrations to Stitch, either as built-in tools or as third-party plugins.

---

## 1. Overview

Stitch supports 9 built-in tools:

- **Cursor** — JSON MCP at `~/.cursor/mcp.json`
- **Claude Code** — MCP via `~/.claude.json` or `claude mcp add`
- **Codex** — TOML MCP at `~/.codex/config.toml` + `AGENTS.md`
- **Windsurf** — JSON MCP at `~/.codeium/windsurf/mcp_config.json`
- **Gemini CLI** — JSON MCP at `~/.gemini/settings.json` + `GEMINI.md`
- **Copilot CLI** — JSON MCP at `~/.copilot/mcp-config.json`
- **Zed** — JSON MCP at `~/.config/zed/settings.json` (uses `context_servers` key)
- **Continue.dev** — Standalone JSON at `~/.continue/mcpServers/stitch.json`
- **Aider** — Instruction-file only via `~/.aider.conf.yml` and `CONVENTIONS.md`

New tools can be added in two ways:

1. **Built-in** — Add to the Stitch package itself (requires modifying `xstitch`).
2. **Third-party plugin** — Register via Python entry points from an external package.

---

## 2. Architecture

Stitch uses an object-oriented hierarchy in `xstitch/global_setup.py`:

### Base Class

```python
class ToolIntegration:
    """Abstract base for all AI tool integrations."""

    name: str = ""

    def is_installed(self) -> tuple[bool, str]:
        """Check if this tool is installed. Returns (detected, method_description)."""
        return False, ""

    def inject_mcp(self, dry_run: bool = False) -> str | None:
        """Inject MCP server config. Returns description or None."""
        return None

    def inject_instructions(self, dry_run: bool = False) -> str | None:
        """Inject instruction files. Returns description or None."""
        return None

    def inject_skills(self, project_path: str, dry_run: bool = False) -> str | None:
        """Install Stitch skill files. Default: None (tool doesn't support skills)."""
        return None

    def get_skill_paths(self) -> list[Path]:
        """Paths where this tool looks for skill files. Default: empty."""
        return []
```

### Mixins

| Mixin | Purpose |
|-------|---------|
| `_PathDetectMixin` | Detection via `_detect_paths` (file/dir existence) and/or `_detect_cmd` (binary in PATH) |
| `_InstructionsMixin` | Injects `GLOBAL_INSTRUCTIONS` into `_instructions_file` (e.g. `AGENTS.md`, `GEMINI.md`) |
| `_SkillsMixin` | Creates project-level skill files in `{project}/{_skill_dir_name}/stitch/SKILL.md` |

### Concrete Tool Classes

| Class | Use Case |
|-------|----------|
| `JsonMcpTool` | Tools with JSON-based MCP config (Cursor, Windsurf, Zed, Gemini CLI, Copilot CLI) |
| `ClaudeCodeTool` | Claude Code — direct config edit + `claude mcp add` CLI fallback |
| `CodexTool` | Codex — TOML-based MCP at `~/.codex/config.toml` |
| `ContinueTool` | Continue.dev — standalone JSON file in `~/.continue/mcpServers/` |
| `AiderTool` | Aider — instruction-file only, no MCP |

---

## 3. Step-by-Step: Adding a Built-in Tool

### 3a. Create the Tool Class

Choose the appropriate base and mixins based on how the tool stores config:

**Example: JSON-based tool (like Cursor)**

```python
# In xstitch/global_setup.py — add to ALL_TOOLS list
JsonMcpTool(
    "MyTool",
    detect_paths=[Path.home() / ".mytool"],
    detect_cmd="mytool",  # optional: also check for binary in PATH
    config_path=Path.home() / ".mytool" / "mcp.json",
    mcp_key="mcpServers",  # or "context_servers" for Zed-like tools
    extra_fields={"source": "custom"},  # optional
    instructions_file=Path.home() / ".mytool" / "INSTRUCTIONS.md",  # optional
    skill_dir=".mytool/skills",  # optional, for tools that support skills
)
```

**Example: Custom class (like Codex)**

```python
class MyToolIntegration(_PathDetectMixin, _InstructionsMixin, ToolIntegration):
    name = "MyTool"
    _detect_paths = [Path.home() / ".mytool"]
    _instructions_file = Path.home() / ".mytool" / "INSTRUCTIONS.md"

    def __init__(self):
        self._config_path = Path.home() / ".mytool" / "config.json"

    def inject_mcp(self, dry_run: bool = False) -> str | None:
        return _inject_json_mcp(self.name, self._config_path, "mcpServers", {}, dry_run)

    def to_registry_dict(self) -> dict:
        return {"name": self.name, "detect_path": self._detect_paths[0]}
```

### 3b. Add to ALL_TOOLS

In `xstitch/global_setup.py`, append your tool to `ALL_TOOLS`:

```python
ALL_TOOLS: list[ToolIntegration] = [
    # ... existing tools ...
    JsonMcpTool("MyTool", detect_paths=[Path.home() / ".mytool"],
                config_path=Path.home() / ".mytool" / "mcp.json"),
]
```

### 3c. Create Re-export Shim

Create `xstitch/integrations/tools/mytool.py`:

```python
"""MyTool integration — JSON-based MCP config at ~/.mytool/mcp.json."""
from ...global_setup import JsonMcpTool  # noqa: F401
```

Or for a custom class:

```python
"""MyTool integration — custom MCP + instruction files."""
from ...global_setup import MyToolIntegration  # noqa: F401
```

### 3d. Register Entry Point

In `xstitch/pyproject.toml`, add under `[project.entry-points."xstitch.integrations"]`:

```toml
[project.entry-points."xstitch.integrations"]
# ... existing ...
mytool = "xstitch.integrations.tools.mytool:JsonMcpTool"
```

For a custom class, use the class name:

```toml
mytool = "xstitch.integrations.tools.mytool:MyToolIntegration"
```

Note: Built-in tools are instantiated in `ALL_TOOLS` with full configuration. The entry point is used for discoverability; third-party plugins use entry points as their sole registration mechanism.

### 3e. Add Instruction File Mapping

In `xstitch/discovery.py`, add your tool to `INJECTION_TARGETS` if it uses project-level instruction files:

```python
INJECTION_TARGETS = [
    # ... existing ...
    {"file": "MYTOOL.md", "agent": "MyTool", "content": "cli", "tool_key": "MyTool"},
]
```

- `content`: `"cli"` for CLI-style instructions (like `CLAUDE.md`), `"mcp"` for MCP-focused (like `.cursorrules`)
- `tool_key` must match `tool.name` for detection-based injection

### 3f. Add Test Coverage

Follow the patterns in `tests/unit/integrations/test_registry.py` and `tests/unit/integrations/test_plugins.py`:

```python
# tests/unit/integrations/test_registry.py
def test_mytool_in_all_tools(self):
    from xstitch.global_setup import ALL_TOOLS
    names = {t.name for t in ALL_TOOLS}
    assert "MyTool" in names

def test_mytool_detection_and_injection(self):
    from xstitch.global_setup import ALL_TOOLS
    mytool = next(t for t in ALL_TOOLS if t.name == "MyTool")
    assert mytool.inject_mcp(dry_run=True) is not None
```

---

## 4. Step-by-Step: Adding a Third-Party Plugin

External packages can register tools without modifying Stitch. The `discover_all_tools()` function in `xstitch/global_setup.py` loads entry points at runtime.

### 4a. Implement Your Tool Class

Your class must inherit from `ToolIntegration` and implement the required methods. It must be instantiable with no arguments (the plugin loader calls `cls()`).

Import base classes from `xstitch.integrations.base`; for `_SkillsMixin` and concrete helpers like `JsonMcpTool`, use `xstitch.global_setup`.

```python
# my_package/stitch_integration.py
from pathlib import Path
from xstitch.integrations.base import ToolIntegration, _PathDetectMixin

class MyToolIntegration(_PathDetectMixin, ToolIntegration):
    name = "MyTool"
    _detect_paths = [Path.home() / ".mytool"]
    _detect_cmd = "mytool"

    def inject_mcp(self, dry_run: bool = False) -> str | None:
        if dry_run:
            return "Would add MCP to ~/.mytool/config.json"
        # Your injection logic
        return "Added to ~/.mytool/config.json"

    def inject_instructions(self, dry_run: bool = False) -> str | None:
        return None  # Or implement if your tool supports instruction files
```

### 4b. Register via Entry Point

In your package's `pyproject.toml`:

```toml
[project.entry-points."xstitch.integrations"]
my_tool = "my_package.stitch_integration:MyToolIntegration"
```

The entry point name (e.g. `my_tool`) is used as the plugin identifier. If it matches a built-in tool's `name` (e.g. `"Cursor"`), the plugin is skipped to avoid duplicates.

### 4c. Installation

Users install your package alongside Stitch:

```bash
pip install xstitch
pip install my-stitch-plugin
```

When they run `python3 -m xstitch.cli global-setup`, `discover_all_tools()` will include your tool. Broken plugins are silently ignored so they never block normal operation.

---

## 5. MCP Integration Patterns

### JSON-based (JsonMcpTool)

Used by: Cursor, Windsurf, Gemini CLI, Copilot CLI, Zed

Config is stored in a JSON file with an `mcpServers` (or tool-specific) key:

```json
{
  "mcpServers": {
    "xstitch": {
      "command": "/usr/bin/python3",
      "args": ["-u", "-m", "xstitch.mcp_server"]
    }
  }
}
```

Zed uses `context_servers` and `extra_fields: {"source": "custom"}`.

### TOML-based (CodexTool)

Used by: Codex

Config is in `~/.codex/config.toml`:

```toml
[mcp_servers.stitch]
command = "/usr/bin/python3"
args = ["-u", "-m", "xstitch.mcp_server"]
startup_timeout_sec = 30
```

### CLI-based (ClaudeCodeTool)

Used by: Claude Code

1. Tries to edit `~/.claude.json` directly (global and per-project `mcpServers`).
2. Falls back to `claude mcp add --transport stdio stitch -- python3 -u -m xstitch.mcp_server`.

### No MCP (AiderTool)

Used by: Aider

No MCP support. Injects `read: [CONVENTIONS.md]` into `~/.aider.conf.yml`. The project-level `CONVENTIONS.md` is created by `python3 -m xstitch.cli inject`.

---

## 6. Detection

`is_installed()` determines whether a tool is present. The `_PathDetectMixin` implements this by:

1. Checking `_detect_paths` — if any path exists, return `(True, "found {path}")`
2. Checking `_detect_cmd` — if the binary is in PATH, return `(True, "found `{cmd}` in PATH")`
3. Otherwise return `(False, "")`

Example paths used by built-in tools:

| Tool | Detection |
|------|-----------|
| Cursor | `~/.cursor` |
| Windsurf | `~/.codeium/windsurf` |
| Zed | `~/.config/zed` |
| Continue.dev | `~/.continue` |
| Claude Code | `claude` in PATH |
| Codex | `~/.codex` |
| Gemini CLI | `~/.gemini` |
| Copilot CLI | `~/.copilot` + `copilot` in PATH |
| Aider | `aider` in PATH |

Override `is_installed()` for custom logic (e.g. checking version, multiple conditions).

---

## 7. Testing

### Registry Tests

In `tests/unit/integrations/test_registry.py`:

- `test_all_tools_has_all_expected_tools` — assert your tool is in the expected set
- `test_registry_backward_compat` — TOOL_REGISTRY matches ALL_TOOLS
- Tool-specific tests (e.g. `test_codex_has_both_mcp_and_instructions`) — verify your tool's capabilities

### Plugin Tests

In `tests/unit/integrations/test_plugins.py`:

- `test_discover_all_tools_includes_builtins` — builtins are always present
- `test_entry_points_loaded_safely` — `_load_entry_point_tools()` returns a list
- `test_broken_plugin_does_not_crash` — broken plugins are ignored
- `test_duplicate_builtin_skipped` — plugins with built-in names are skipped

### Skills Tests (if applicable)

```python
def test_mytool_has_skills(self):
    from xstitch.global_setup import ALL_TOOLS
    mytool = next(t for t in ALL_TOOLS if t.name == "MyTool")
    paths = mytool.get_skill_paths()
    assert len(paths) > 0

def test_inject_skills_dry_run(self, tmp_path):
    from xstitch.global_setup import ALL_TOOLS
    mytool = next(t for t in ALL_TOOLS if t.name == "MyTool")
    result = mytool.inject_skills(str(tmp_path), dry_run=True)
    assert "Would create" in result or result is None
```

### Integration Tests

For tool-specific behavior, add tests in `tests/integration/` (see `test_tool_isolation.py` for examples of testing injection without affecting other tools).
