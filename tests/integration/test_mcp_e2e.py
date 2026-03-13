"""End-to-end MCP protocol tests using real subprocess communication."""

from __future__ import annotations

import json
import select
import subprocess
import sys

import pytest


class TestMCPProtocolE2E:
    """End-to-end MCP protocol tests using real subprocess communication."""

    def test_full_mcp_conversation(self, tmp_path):
        """Complete MCP conversation: init → tools/list → create → snapshot → decision → verify."""
        import subprocess, select
        proc = subprocess.Popen(
            [sys.executable, "-u", "-m", "xstitch.mcp_server", "--project", str(tmp_path)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )

        def send(req):
            body = json.dumps(req).encode("utf-8")
            header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
            proc.stdin.write(header + body)
            proc.stdin.flush()

        def recv(timeout=5):
            ready, _, _ = select.select([proc.stdout], [], [], timeout)
            if not ready:
                return None
            buf = b""
            while b"\r\n\r\n" not in buf:
                byte = proc.stdout.read(1)
                if not byte:
                    return None
                buf += byte
            length = int(buf.decode().split(":")[1].split("\r\n")[0].strip())
            body = proc.stdout.read(length)
            return json.loads(body.decode())

        try:
            # 1. Initialize
            send({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
            resp = recv()
            assert resp["result"]["serverInfo"]["name"] == "xstitch"

            # 2. tools/list
            send({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
            resp = recv()
            tool_names = {t["name"] for t in resp["result"]["tools"]}
            assert "stitch_auto_route" in tool_names
            assert "stitch_snapshot" in tool_names
            assert len(tool_names) == 14

            # 3. Create a task (triggers lazy Store init)
            send({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {
                "name": "stitch_create_task",
                "arguments": {"title": "E2E Test Task", "objective": "Verify protocol"}
            }})
            resp = recv()
            assert "Created task" in resp["result"]["content"][0]["text"]

            # 4. Snapshot (verifies lazy import of capture_snapshot)
            send({"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {
                "name": "stitch_snapshot",
                "arguments": {"message": "Verified MCP protocol works end-to-end correctly"}
            }})
            resp = recv()
            assert "Snapshot saved" in resp["result"]["content"][0]["text"]

            # 5. Decision (verifies lazy import of Decision model)
            send({"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {
                "name": "stitch_add_decision",
                "arguments": {"problem": "Testing approach", "chosen": "Subprocess E2E"}
            }})
            resp = recv()
            assert "Decision logged" in resp["result"]["content"][0]["text"]

            # 6. Verify data persisted via get_task
            send({"jsonrpc": "2.0", "id": 6, "method": "tools/call", "params": {
                "name": "stitch_get_task", "arguments": {"task_id": "active"}
            }})
            resp = recv()
            task_text = resp["result"]["content"][0]["text"]
            assert "E2E Test Task" in task_text
            assert "Decisions (1)" in task_text
            assert "Recent snapshots" in task_text

            # 7. Unknown method returns proper error
            send({"jsonrpc": "2.0", "id": 7, "method": "nonexistent", "params": {}})
            resp = recv()
            assert "error" in resp
            assert resp["error"]["code"] == -32601

        finally:
            proc.kill()
            proc.wait()

    def test_content_length_counts_bytes_not_chars(self, tmp_path):
        """Multi-byte UTF-8 (emoji/accents) must not corrupt Content-Length framing."""
        import subprocess, select
        proc = subprocess.Popen(
            [sys.executable, "-u", "-m", "xstitch.mcp_server", "--project", str(tmp_path)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )

        def send(req):
            body = json.dumps(req).encode("utf-8")
            header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
            proc.stdin.write(header + body)
            proc.stdin.flush()

        def recv(timeout=5):
            ready, _, _ = select.select([proc.stdout], [], [], timeout)
            if not ready:
                return None
            buf = b""
            while b"\r\n\r\n" not in buf:
                byte = proc.stdout.read(1)
                if not byte:
                    return None
                buf += byte
            length = int(buf.decode().split(":")[1].split("\r\n")[0].strip())
            body = proc.stdout.read(length)
            return json.loads(body.decode())

        try:
            send({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
            recv()

            # Create task with multi-byte title (emoji + accents)
            title = "Tâche résumé 🚀 données décision"
            send({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {
                "name": "stitch_create_task",
                "arguments": {"title": title}
            }})
            resp = recv()
            assert title in resp["result"]["content"][0]["text"]

            # Verify we can still communicate after multi-byte payload
            send({"jsonrpc": "2.0", "id": 3, "method": "ping", "params": {}})
            resp = recv()
            assert resp["result"] == {}

        finally:
            proc.kill()
            proc.wait()
