"""Tool integrations for Stitch.

This package organizes AI tool integration code into a clear structure:
  - base: ToolIntegration ABC and mixins
  - registry: Tool discovery, ALL_TOOLS list, detect_tools
  - discovery: Project-level instruction injection
  - enforcement: Claude Code hooks
  - tools/: Per-tool integration classes (re-exports from registry)

Why per-tool isolation:
  - Adding Copilot support never touches Cursor code
  - Tool-specific bugs are localized to one file
  - Contributors only need to understand one tool's integration

Implementation note: actual code lives in xstitch.global_setup, xstitch.discovery,
and xstitch.enforcement. This package re-exports from those modules.
"""

from ..global_setup import (  # noqa: F401
    ToolIntegration,
    _PathDetectMixin,
    _InstructionsMixin,
    JsonMcpTool,
    ClaudeCodeTool,
    ContinueTool,
    CodexTool,
    AiderTool,
    ALL_TOOLS,
    TOOL_REGISTRY,
    detect_tools,
    discover_all_tools,
    inject_mcp_for_tool,
    inject_instructions_for_tool,
    global_setup,
    generate_bootstrap,
    GLOBAL_HOME,
    PYTHON_BIN,
    MCP_SERVER_ENTRY,
    GLOBAL_INSTRUCTIONS,
)
from ..discovery import (  # noqa: F401
    Stitch_SECTION_MARKER,
    INJECTION_TARGETS,
    inject_agent_discovery,
    get_injected_file_paths,
)
from ..enforcement import (  # noqa: F401
    generate_claude_code_hooks,
    install_claude_code_hooks,
    install_claude_code_hooks_global,
    check_claude_code_hooks,
)
