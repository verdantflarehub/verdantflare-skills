#!/usr/bin/env python3
"""Unit tests for agy_mcp_server.py and install.py."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import agy_mcp_server  # noqa: E402
import install as agy_install  # noqa: E402


class TestAgyMcpServer(unittest.TestCase):
    def test_workspace_uri_conversion_and_normalization(self) -> None:
        uri = agy_mcp_server.path_to_workspace_uri("C:/Users/Test/project")
        self.assertTrue(uri.startswith("file:///"))
        norm1 = agy_mcp_server.normalize_uri_for_compare("file:///c%3A/Users/Test/project/")
        norm2 = agy_mcp_server.normalize_uri_for_compare(r"C:\Users\Test\project")
        self.assertEqual(norm1, norm2)

    def test_extract_trajectory_digest(self) -> None:
        fake_traj = {
            "status": "CASCADE_RUN_STATUS_IDLE",
            "numTotalSteps": 2,
            "trajectory": {
                "steps": [
                    {
                        "type": "CORTEX_STEP_TYPE_USER_INPUT",
                        "userInput": {"items": [{"text": "Hello Gemini"}]},
                    },
                    {
                        "type": "CORTEX_STEP_TYPE_PLANNER_RESPONSE",
                        "plannerResponse": {
                            "thinking": "Reasoning step",
                            "response": "Draft reply",
                            "modifiedResponse": "Final reply",
                            "toolCalls": [{"name": "read_file", "argumentsJson": '{"Path":"a.py"}'}],
                        },
                    },
                ]
            },
        }
        digest = agy_mcp_server.extract_trajectory_digest(fake_traj)
        self.assertEqual(digest["status"], "CASCADE_RUN_STATUS_IDLE")
        self.assertEqual(digest["last_user_input"], "Hello Gemini")
        self.assertEqual(digest["last_response"], "Final reply")
        self.assertEqual(len(digest["recent_tool_calls"]), 1)
        self.assertEqual(digest["recent_tool_calls"][0]["name"], "read_file")

    def test_mcp_jsonrpc_handlers(self) -> None:
        init_resp = agy_mcp_server.handle_mcp_request(
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2024-11-05"}}
        )
        self.assertIsNotNone(init_resp)
        assert init_resp is not None
        self.assertEqual(init_resp["result"]["serverInfo"]["name"], "verdantflare-agent-antigravity")

        notif_resp = agy_mcp_server.handle_mcp_request({"jsonrpc": "2.0", "method": "notifications/initialized"})
        self.assertIsNone(notif_resp)

        list_resp = agy_mcp_server.handle_mcp_request({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        assert list_resp is not None
        tool_names = {t["name"] for t in list_resp["result"]["tools"]}
        self.assertIn("agy_dispatch_task", tool_names)
        self.assertIn("agy_list_conversations", tool_names)
        self.assertIn("agy_get_conversation", tool_names)

    def test_stdio_ndjson_and_content_length(self) -> None:
        server_py = SCRIPT_DIR / "agy_mcp_server.py"
        # 1. NDJSON
        req_line = json.dumps({"jsonrpc": "2.0", "id": 10, "method": "tools/list"}) + "\n"
        proc = subprocess.run(
            [sys.executable, "-X", "utf8", str(server_py), "mcp"],
            input=req_line.encode("utf-8"),
            capture_output=True,
            timeout=10,
            check=True,
        )
        resp = json.loads(proc.stdout.decode("utf-8").strip())
        self.assertEqual(resp["id"], 10)
        self.assertEqual(len(resp["result"]["tools"]), 6)

        # 2. Content-Length framing (with extra header before Content-Length) + malformed JSON recovery
        body = json.dumps({"jsonrpc": "2.0", "id": 11, "method": "ping"}).encode("utf-8")
        framed = f"Content-Type: application/vscode-jsonrpc; charset=utf-8\r\nContent-Length: {len(body)}\r\n\r\n".encode("ascii") + body
        proc2 = subprocess.run(
            [sys.executable, "-X", "utf8", str(server_py), "mcp"],
            input=framed,
            capture_output=True,
            timeout=10,
            check=True,
        )
        raw_out = proc2.stdout
        self.assertTrue(raw_out.startswith(b"Content-Length:"))
        _, json_part = raw_out.split(b"\r\n\r\n", 1)
        resp2 = json.loads(json_part.decode("utf-8"))
        self.assertEqual(resp2["id"], 11)

        # 3. Malformed JSON line followed by valid NDJSON request should return -32700 and stay alive
        multi_input = b"{bad json line\n" + req_line.encode("utf-8")
        proc3 = subprocess.run(
            [sys.executable, "-X", "utf8", str(server_py), "mcp"],
            input=multi_input,
            capture_output=True,
            timeout=10,
            check=True,
        )
        out_lines = [ln for ln in proc3.stdout.decode("utf-8").splitlines() if ln.strip()]
        self.assertEqual(len(out_lines), 2)
        err_resp = json.loads(out_lines[0])
        ok_resp = json.loads(out_lines[1])
        self.assertEqual(err_resp["error"]["code"], -32700)
        self.assertEqual(ok_resp["id"], 10)

    def test_select_hub_workspace_matching_and_fallback(self) -> None:
        ws_a = agy_mcp_server.path_to_workspace_uri("C:/Users/Test/repo-a")
        ws_b = agy_mcp_server.path_to_workspace_uri("C:/Users/Test/repo-b")
        fake_hubs = [
            {"address": "localhost:1001", "csrf_token": "t1", "workspaces": [ws_a], "summaries": {"c1": {}}},
            {"address": "localhost:1002", "csrf_token": "t2", "workspaces": [ws_b], "summaries": {"c2": {}}},
            {"address": "localhost:1003", "csrf_token": "t3", "workspaces": [], "summaries": {}},
        ]
        # Exact match
        h1 = agy_mcp_server.select_hub(workspace="C:/Users/Test/repo-b", hubs=fake_hubs)
        self.assertEqual(h1["address"], "localhost:1002")
        self.assertTrue(h1["workspace_matched"])
        self.assertEqual(h1["matched_by"], "workspace_exact")

        # Subdirectory prefix match
        h2 = agy_mcp_server.select_hub(workspace="C:/Users/Test/repo-a/sub/dir", hubs=fake_hubs)
        self.assertEqual(h2["address"], "localhost:1001")
        self.assertTrue(h2["workspace_matched"])
        self.assertEqual(h2["matched_by"], "workspace_prefix")

        # Unmatched workspace prefers empty hub fallback and sets workspace_matched=False
        h3 = agy_mcp_server.select_hub(workspace="C:/Users/Test/repo-unknown", hubs=fake_hubs)
        self.assertEqual(h3["address"], "localhost:1003")
        self.assertFalse(h3["workspace_matched"])
        self.assertEqual(h3["matched_by"], "empty_hub_fallback")

        # Strict workspace raises RuntimeError when unmatched
        with self.assertRaises(RuntimeError):
            agy_mcp_server.select_hub(
                workspace="C:/Users/Test/repo-unknown",
                hubs=fake_hubs,
                strict_workspace=True,
            )

    def test_windows_process_discovery_powershell_fallback(self) -> None:
        calls: list[str] = []

        def fake_check_output(cmd: list[str], **kwargs: object) -> str:
            calls.append(cmd[0])
            if cmd[0] == "pwsh":
                raise FileNotFoundError("pwsh not found")
            return json.dumps({"ProcessId": 4321, "CommandLine": "agy.exe --hub --hub-port=19999 --csrf_token=abc"})

        with mock.patch("subprocess.check_output", side_effect=fake_check_output):
            procs = agy_mcp_server._query_windows_agy_processes()
        self.assertEqual(calls, ["pwsh", "powershell.exe"])
        self.assertEqual(len(procs), 1)
        self.assertEqual(procs[0]["ProcessId"], 4321)

    def test_install_and_mcp_registration_in_tempdir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            skills_root = tmp_path / "skills"
            target = agy_install.install(skills_root)
            self.assertTrue((target / "SKILL.md").is_file())
            self.assertTrue((target / "scripts" / "agy_mcp_server.py").is_file())

            claude_cfg = tmp_path / ".claude.json"
            codex_cfg = tmp_path / "config.toml"
            server_script = target / "scripts" / "agy_mcp_server.py"
            agy_install.register_claude_mcp(server_script, claude_cfg)
            agy_install.register_codex_mcp(server_script, codex_cfg)

            claude_data = json.loads(claude_cfg.read_text(encoding="utf-8"))
            self.assertIn("antigravity", claude_data["mcpServers"])
            codex_text = codex_cfg.read_text(encoding="utf-8")
            self.assertIn("[mcp_servers.antigravity]", codex_text)


if __name__ == "__main__":
    unittest.main()
