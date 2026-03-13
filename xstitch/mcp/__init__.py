"""MCP (Model Context Protocol) server for Stitch.

This package provides organized import paths for the MCP server:
  - server: StitchServer class and run_server function
  - transport: Dual-protocol I/O (NDJSON + Content-Length)
  - tools: TOOLS list and tool dispatch

Architecture:
  - Dual-protocol auto-detection: reads the first byte from stdin to determine
    whether the client uses NDJSON (Codex) or Content-Length framing (Cursor).
  - Lazy Store initialization: Store is created on first tool call, not at
    server startup, to avoid blocking the MCP handshake.
  - All I/O is unbuffered binary to prevent Python's buffering from causing
    MCP timeouts.

Implementation note: actual code lives in xstitch.mcp_server.
This package re-exports from that module.
"""

from ..mcp_server import (  # noqa: F401
    StitchServer,
    TOOLS,
    run_server,
)
