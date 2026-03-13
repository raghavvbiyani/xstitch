"""Unit tests for Stitch MCP transport protocol detection."""

from __future__ import annotations

import io
import json
from unittest.mock import patch

import pytest


class TestMCPDualProtocol:
    """Verify the server works with both NDJSON (Codex) and Content-Length (Cursor)."""

    def test_ndjson_full_conversation(self, tmp_path):
        """Full MCP conversation using NDJSON transport (Codex/rmcp format)."""
        import subprocess, select
        import sys
        proc = subprocess.Popen(
            [sys.executable, "-u", "-m", "xstitch.mcp_server", "--project", str(tmp_path)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )

        def send(req):
            proc.stdin.write(json.dumps(req).encode("utf-8") + b"\n")
            proc.stdin.flush()

        def recv(timeout=5):
            ready, _, _ = select.select([proc.stdout], [], [], timeout)
            if not ready:
                return None
            line = proc.stdout.readline()
            return json.loads(line.decode("utf-8").strip()) if line else None

        try:
            send({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
                "protocolVersion": "2025-06-18", "capabilities": {},
                "clientInfo": {"name": "codex-mcp-client", "version": "0.114.0"}
            }})
            resp = recv()
            assert resp["result"]["protocolVersion"] == "2025-06-18"
            assert resp["result"]["serverInfo"]["name"] == "xstitch"

            send({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
            resp = recv()
            assert len(resp["result"]["tools"]) == 14

            send({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {
                "name": "stitch_create_task",
                "arguments": {"title": "NDJSON Protocol Test"}
            }})
            resp = recv()
            assert "Created task" in resp["result"]["content"][0]["text"]

            send({"jsonrpc": "2.0", "id": 4, "method": "ping", "params": {}})
            resp = recv()
            assert resp["result"] == {}

        finally:
            proc.kill()
            proc.wait()

    def test_protocol_version_echo(self, tmp_path):
        """Server echoes the client's protocol version for compatibility."""
        from xstitch.mcp_server import StitchServer
        server = StitchServer(str(tmp_path))

        resp = server.handle_request({
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-06-18"}
        })
        assert resp["result"]["protocolVersion"] == "2025-06-18"

        server2 = StitchServer(str(tmp_path))
        resp2 = server2.handle_request({
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2024-11-05"}
        })
        assert resp2["result"]["protocolVersion"] == "2024-11-05"

    def test_auto_detect_ndjson_from_first_byte(self):
        """_read() auto-detects NDJSON when first byte is '{'."""
        import xstitch.mcp_server as mod
        import io

        original_stdin = mod._stdin
        original_transport = mod._transport

        try:
            msg = {"jsonrpc": "2.0", "id": 1, "method": "ping", "params": {}}
            ndjson_line = json.dumps(msg).encode("utf-8") + b"\n"
            mod._stdin = io.BytesIO(ndjson_line)
            mod._transport = ""

            result = mod._read()
            assert result == msg
            assert mod._transport == "ndjson"
        finally:
            mod._stdin = original_stdin
            mod._transport = original_transport

    def test_auto_detect_content_length_from_header(self):
        """_read() auto-detects Content-Length when first line starts with 'C'."""
        import xstitch.mcp_server as mod
        import io

        original_stdin = mod._stdin
        original_transport = mod._transport

        try:
            msg = {"jsonrpc": "2.0", "id": 1, "method": "ping", "params": {}}
            body = json.dumps(msg).encode("utf-8")
            framed = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii") + body
            mod._stdin = io.BytesIO(framed)
            mod._transport = ""

            result = mod._read()
            assert result == msg
            assert mod._transport == "content-length"
        finally:
            mod._stdin = original_stdin
            mod._transport = original_transport
