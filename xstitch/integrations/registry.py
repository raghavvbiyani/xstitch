"""Re-export shim — actual code in xstitch.global_setup.

Provides: ALL_TOOLS, TOOL_REGISTRY, detect_tools, discover_all_tools, and tool injection functions.
"""

from ..global_setup import (  # noqa: F401
    ALL_TOOLS,
    TOOL_REGISTRY,
    detect_tools,
    discover_all_tools,
    inject_mcp_for_tool,
    inject_instructions_for_tool,
)
