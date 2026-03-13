"""Unit tests for Stitch MCP server."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest


class TestMCPServerStartup:
    """Verify MCP server handles initialization correctly."""

    def test_lazy_store_initialization(self):
        """Store should not be created until first tool call."""
        from xstitch.mcp_server import StitchServer
        server = StitchServer("/tmp/test-lazy-init")
        assert server._store is None
        resp = server.handle_request({
            "jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}
        })
        assert server._store is None  # Still lazy after initialize
        assert resp["result"]["serverInfo"]["name"] == "xstitch"

    def test_tools_list_does_not_create_store(self):
        """tools/list should respond without creating Store."""
        from xstitch.mcp_server import StitchServer
        server = StitchServer("/tmp/test-tools-list")
        resp = server.handle_request({
            "jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}
        })
        assert server._store is None
        assert len(resp["result"]["tools"]) > 0

    def test_ping_responds_instantly(self):
        """ping should respond without creating Store."""
        from xstitch.mcp_server import StitchServer
        server = StitchServer("/tmp/test-ping")
        resp = server.handle_request({
            "jsonrpc": "2.0", "id": 3, "method": "ping", "params": {}
        })
        assert server._store is None
        assert resp["result"] == {}

    def test_mcp_entry_has_unbuffered_flag(self):
        """MCP_SERVER_ENTRY must include -u for unbuffered I/O."""
        from xstitch.global_setup import MCP_SERVER_ENTRY
        assert "-u" in MCP_SERVER_ENTRY["args"]
        assert "-m" in MCP_SERVER_ENTRY["args"]
        assert "xstitch.mcp_server" in MCP_SERVER_ENTRY["args"]
