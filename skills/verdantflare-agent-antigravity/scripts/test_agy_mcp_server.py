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
        self.assertEqual(len(resp["result"]["tools"]), 9)

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
            claude_cfg.write_text(
                json.dumps({"mcpServers": {"antigravity": {"command": "old"}, "keep-me": {"command": "ok"}}}),
                encoding="utf-8",
            )
            codex_cfg = tmp_path / "config.toml"
            codex_cfg.write_text(
                '[mcp_servers.antigravity]\ncommand = "old"\n\n[mcp_servers.keep-me]\ncommand = "ok"\n',
                encoding="utf-8",
            )
            server_script = target / "scripts" / "agy_mcp_server.py"
            agy_install.register_claude_mcp(server_script, claude_cfg)
            agy_install.register_codex_mcp(server_script, codex_cfg)

            claude_data = json.loads(claude_cfg.read_text(encoding="utf-8"))
            self.assertIn("verdantflare-agent-antigravity", claude_data["mcpServers"])
            self.assertNotIn("antigravity", claude_data["mcpServers"])
            self.assertIn("keep-me", claude_data["mcpServers"])
            codex_text = codex_cfg.read_text(encoding="utf-8")
            self.assertIn("[mcp_servers.verdantflare-agent-antigravity]", codex_text)
            self.assertNotIn("[mcp_servers.antigravity]", codex_text)
            self.assertIn("[mcp_servers.keep-me]", codex_text)

    def test_resolve_project_id_and_dispatch_project_env_config(self) -> None:
        home_uri = agy_mcp_server.path_to_workspace_uri(Path.home())
        repo_uri = agy_mcp_server.path_to_workspace_uri("C:/Users/Test/go/src/github.com/org/design-repo")
        sub_dir = "C:/Users/Test/go/src/github.com/org/design-repo/subpkg"

        def fake_lsrpc(address: str, csrf: str, method: str, payload: dict | None = None, timeout: float = 15.0) -> dict:
            if method == "ListProjects":
                return {"projectIds": ["p-home", "p-repo", "p-archived", "outside-of-project"]}
            if method == "ReadProjects":
                return {
                    "projects": [
                        {
                            "id": "p-home",
                            "name": "Home",
                            "projectResources": {"resources": [{"folderUri": home_uri}]},
                        },
                        {
                            "id": "p-repo",
                            "name": "design-repo",
                            "projectResources": {"resources": [{"gitFolder": {"folderUri": repo_uri}}]},
                        },
                        {
                            "id": "p-archived",
                            "name": "archived-repo",
                            "archived": True,
                            "projectResources": {"resources": [{"folderUri": repo_uri}]},
                        },
                    ]
                }
            if method == "StartCascade":
                self.assertNotIn("workspaceUris", payload or {})
                self.assertEqual(
                    (payload or {}).get("projectEnvConfig"),
                    {"projectId": "p-repo", "defaultProjectEnvironment": {}},
                )
                return {"cascadeId": "cid-new-123"}
            if method == "SendUserCascadeMessage":
                return {}
            if method == "SetBrowserOpenConversation":
                return {}
            raise AssertionError(f"Unexpected RPC method: {method}")

        with mock.patch.object(agy_mcp_server, "lsrpc_call", side_effect=fake_lsrpc):
            # Exact match
            self.assertEqual(agy_mcp_server.resolve_project_id("localhost:1", "tok", repo_uri), "p-repo")
            # Subdirectory inside repo matches p-repo
            self.assertEqual(agy_mcp_server.resolve_project_id("localhost:1", "tok", sub_dir), "p-repo")
            # Exact match on home directory matches p-home, but unrelated child under home does not
            self.assertEqual(agy_mcp_server.resolve_project_id("localhost:1", "tok", home_uri), "p-home")
            unrelated_under_home = Path.home() / "unregistered_workspace_xyz"
            self.assertIsNone(agy_mcp_server.resolve_project_id("localhost:1", "tok", str(unrelated_under_home)))

            fake_hub = {
                "address": "localhost:1",
                "csrf_token": "tok",
                "workspaces": [repo_uri],
                "workspace_matched": True,
                "matched_by": "workspace_exact",
                "summaries": {},
            }
            with mock.patch.object(agy_mcp_server, "select_hub", return_value=fake_hub):
                disp = agy_mcp_server.tool_agy_dispatch_task(
                    prompt="Ping",
                    workspace="C:/Users/Test/go/src/github.com/org/design-repo",
                    mode="panel",
                    wait=False,
                )
                self.assertTrue(disp["ok"])
                self.assertEqual(disp["conversation_id"], "cid-new-123")
                self.assertEqual(disp["project_id"], "p-repo")

    def test_cancel_already_idle_conversation_still_deletes(self) -> None:
        deleted_ids: list[str] = []

        def fake_lsrpc(address: str, csrf: str, method: str, payload: dict | None = None, timeout: float = 15.0) -> dict:
            if method == "CancelCascadeInvocation":
                raise RuntimeError('LS RPC CancelCascadeInvocation HTTP 500: {"message":"executor is not currently running"}')
            if method == "DeleteCascadeTrajectory":
                deleted_ids.append((payload or {})["cascadeId"])
                return {}
            raise AssertionError(f"Unexpected RPC method: {method}")

        fake_hub = {"address": "localhost:1", "csrf_token": "tok", "summaries": {"cid-idle": {}}}
        with mock.patch.object(agy_mcp_server, "select_hub", return_value=fake_hub):
            with mock.patch.object(agy_mcp_server, "lsrpc_call", side_effect=fake_lsrpc):
                res = agy_mcp_server.tool_agy_cancel_conversation("cid-idle", delete_trajectory=True)
        self.assertTrue(res["ok"])
        self.assertFalse(res["cancelled"])
        self.assertTrue(res["deleted"])
        self.assertEqual(deleted_ids, ["cid-idle"])

    def test_discover_hubs_daemon_file_without_cmdline_csrf_and_legacy_compat(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            gemini_home = Path(tmp) / ".gemini"
            daemon_dir = gemini_home / "antigravity" / "daemon"
            daemon_dir.mkdir(parents=True)
            ws_new = Path(tmp) / "workspace-new"
            ws_legacy = Path(tmp) / "workspace-legacy"
            ws_new.mkdir()
            ws_legacy.mkdir()

            (daemon_dir / "hub_63183.json").write_text(
                json.dumps(
                    {
                        "pid": 3468,
                        "httpsPort": 63182,
                        "httpPort": 63183,
                        "lsVersion": "2.31.0",
                        "csrfToken": "daemon-csrf-token-1",
                    }
                ),
                encoding="utf-8",
            )

            fake_procs = [
                {
                    "ProcessId": 3468,
                    "CommandLine": f'agy.exe --hub --hub-port=63183 --app_data_dir=antigravity --add-dir="{ws_new}"',
                },
                {
                    "ProcessId": 4001,
                    "CommandLine": f'agy.exe --hub --hub-port=59001 --csrf_token=legacy-csrf-token-2 --add-dir="{ws_legacy}"',
                },
                {
                    "ProcessId": 5002,
                    "CommandLine": "agy.exe --input-format stream-json --output-format stream-json",
                },
            ]

            seen_tokens: dict[str, str] = {}

            def fake_lsrpc(address: str, csrf: str, method: str, payload: dict | None = None, timeout: float = 15.0) -> dict:
                self.assertEqual(method, "GetAllCascadeTrajectories")
                seen_tokens[address] = csrf
                return {"trajectorySummaries": {}}

            clean_env = {
                "AGY_GEMINI_HOME": str(gemini_home),
                "ANTIGRAVITY_LS_ADDRESS": "",
                "ANTIGRAVITY_CSRF_TOKEN": "",
            }
            with (
                mock.patch.dict("os.environ", clean_env, clear=False),
                mock.patch.object(agy_mcp_server.os, "name", "nt"),
                mock.patch.object(agy_mcp_server, "_query_windows_agy_processes", return_value=fake_procs),
                mock.patch.object(agy_mcp_server, "lsrpc_call", side_effect=fake_lsrpc),
            ):
                hubs = agy_mcp_server.discover_hubs()

            self.assertEqual(len(hubs), 2)
            by_addr = {h["address"]: h for h in hubs}
            self.assertEqual(seen_tokens["localhost:63183"], "daemon-csrf-token-1")
            self.assertEqual(seen_tokens["localhost:59001"], "legacy-csrf-token-2")
            self.assertEqual(by_addr["localhost:63183"]["source"], "process+daemon")
            self.assertEqual(by_addr["localhost:63183"]["ls_version"], "2.31.0")
            expected_ws_uri = agy_mcp_server.path_to_workspace_uri(ws_new)
            self.assertEqual(by_addr["localhost:63183"]["mounted_workspaces"], [expected_ws_uri])
            self.assertEqual(by_addr["localhost:63183"]["workspaces"], [expected_ws_uri])
            self.assertEqual(by_addr["localhost:59001"]["source"], "process")

            selected = agy_mcp_server.select_hub(workspace=str(ws_new), hubs=hubs, strict_workspace=True)
            self.assertEqual(selected["address"], "localhost:63183")
            self.assertEqual(selected["matched_by"], "workspace_exact")

    def test_discover_hubs_daemon_fallback_excludes_cli_and_dead_pids(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            gemini_home = Path(tmp) / ".gemini"
            daemon_dir = gemini_home / "antigravity" / "daemon"
            daemon_dir.mkdir(parents=True)

            # 1. Valid live Hub PID
            (daemon_dir / "hub_61001.json").write_text(
                json.dumps({"pid": 1001, "httpPort": 61001, "lsVersion": "2.31.0", "csrfToken": "tok-live-hub"}),
                encoding="utf-8",
            )
            # 2. Ephemeral non-hub CLI worker PID (must be excluded)
            (daemon_dir / "hub_61002.json").write_text(
                json.dumps({"pid": 2002, "httpPort": 61002, "lsVersion": "2.31.0", "csrfToken": "tok-cli-worker"}),
                encoding="utf-8",
            )
            # 3. Stale dead PID (must be excluded)
            (daemon_dir / "hub_61003.json").write_text(
                json.dumps({"pid": 3003, "httpPort": 61003, "lsVersion": "2.31.0", "csrfToken": "tok-dead"}),
                encoding="utf-8",
            )

            # Simulate process scan seeing only the non-hub CLI worker (e.g. Hub cmdline unavailable)
            fake_procs = [
                {"ProcessId": 2002, "CommandLine": "agy.exe --input-format stream-json --output-format stream-json"},
            ]

            def fake_pid_alive(pid: int | None) -> bool:
                return pid in {1001, 2002}

            clean_env = {
                "AGY_GEMINI_HOME": str(gemini_home),
                "ANTIGRAVITY_LS_ADDRESS": "",
                "ANTIGRAVITY_CSRF_TOKEN": "",
            }
            with (
                mock.patch.dict("os.environ", clean_env, clear=False),
                mock.patch.object(agy_mcp_server.os, "name", "nt"),
                mock.patch.object(agy_mcp_server, "_query_windows_agy_processes", return_value=fake_procs),
                mock.patch.object(agy_mcp_server, "_is_pid_alive", side_effect=fake_pid_alive),
                mock.patch.object(agy_mcp_server, "lsrpc_call", return_value={"trajectorySummaries": {}}),
            ):
                hubs = agy_mcp_server.discover_hubs()

            self.assertEqual([h["address"] for h in hubs], ["localhost:61001"])
            self.assertEqual(hubs[0]["source"], "daemon")
            self.assertEqual(hubs[0]["csrf_token"], "tok-live-hub")

    def test_cli_dispatch_stream_json_stdin_isolation_and_explicit_errors(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            gemini_home = tmp_path / ".gemini"
            ws_dir = tmp_path / "workspace"
            ws_dir.mkdir(parents=True)
            fake_agy = tmp_path / ("fake_agy.cmd" if sys.platform == "win32" else "fake_agy.sh")
            driver_py = tmp_path / "fake_agy_driver.py"
            driver_py.write_text(
                "\n".join(
                    [
                        "import json, sys",
                        "from pathlib import Path",
                        "args = sys.argv[1:]",
                        "log_file = args[args.index('--log-file') + 1] if '--log-file' in args else ''",
                        "model = args[args.index('--model') + 1] if '--model' in args else ''",
                        "line = sys.stdin.readline()",
                        "evt = json.loads(line)",
                        "prompt = evt['message']['content']",
                        "if model == 'bad-model':",
                        "    print(json.dumps({'event': 'result', 'result': {'conversation_id': '', 'status': 'ERROR', 'error': 'Unknown model bad-model'}}))",
                        "    sys.exit(0)",
                        "if prompt == 'silent-crash':",
                        "    if log_file:",
                        "        Path(log_file).write_text('E1112 21:00:00 server.go:100] fatal auth token expired\\n', encoding='utf-8')",
                        "    sys.exit(1)",
                        "if prompt == 'slow-run':",
                        "    import time; time.sleep(0.8)",
                        "print(json.dumps({'event': 'result', 'result': {'conversation_id': '11111111-2222-3333-4444-555555555555', 'status': 'SUCCESS', 'response': 'CLI_OK:' + prompt}}))",
                    ]
                ),
                encoding="utf-8",
            )
            if sys.platform == "win32":
                fake_agy.write_text(f'@"{sys.executable}" -X utf8 "{driver_py}" %*\r\n', encoding="utf-8")
            else:
                fake_agy.write_text(f'#!/bin/sh\nexec "{sys.executable}" -X utf8 "{driver_py}" "$@"\n', encoding="utf-8")
                fake_agy.chmod(0o755)

            env_overrides = {
                "AGY_GEMINI_HOME": str(gemini_home),
                "AGY_BINARY": str(fake_agy),
            }
            with (
                mock.patch.dict("os.environ", env_overrides, clear=False),
                mock.patch.object(agy_mcp_server, "_CLI_ASYNC_STARTUP_CHECK_SEC", 0.6),
            ):
                # 1. Sync CLI success
                ok_sync = agy_mcp_server.tool_agy_dispatch_task(
                    prompt="hello-sync",
                    workspace=str(ws_dir),
                    mode="cli",
                    wait=True,
                )
                self.assertTrue(ok_sync["ok"])
                self.assertEqual(ok_sync["conversation_id"], "11111111-2222-3333-4444-555555555555")
                self.assertEqual(ok_sync["last_response"], "CLI_OK:hello-sync")

                # 2. Structured JSON ERROR on invalid model -> ok=False & MCP isError=True
                mcp_err = agy_mcp_server.handle_mcp_request(
                    {
                        "jsonrpc": "2.0",
                        "id": 91,
                        "method": "tools/call",
                        "params": {
                            "name": "agy_dispatch_task",
                            "arguments": {
                                "prompt": "hello",
                                "workspace": str(ws_dir),
                                "mode": "cli",
                                "model": "bad-model",
                                "wait": True,
                            },
                        },
                    }
                )
                assert mcp_err is not None
                self.assertTrue(mcp_err["result"]["isError"])
                err_payload = json.loads(mcp_err["result"]["content"][0]["text"])
                self.assertFalse(err_payload["ok"])
                self.assertIn("Unknown model bad-model", err_payload["error"])

                # 3. Silent crash with empty stdout/stderr falls back to --log-file tail in both wait=True and wait=False
                crash_async = agy_mcp_server.tool_agy_dispatch_task(
                    prompt="silent-crash",
                    workspace=str(ws_dir),
                    mode="cli",
                    wait=False,
                )
                self.assertFalse(crash_async["ok"])
                self.assertIn("fatal auth token expired", crash_async["error"])

                exit_code = agy_mcp_server.main(
                    ["dispatch", "silent-crash", "--workspace", str(ws_dir), "--mode", "cli"]
                )
                self.assertEqual(exit_code, 1)

                # 4. Still-running async CLI keeps stdin file open on Windows without WinError 32
                import time
                import warnings

                with warnings.catch_warnings():
                    warnings.simplefilter("ignore", ResourceWarning)
                    with mock.patch.object(agy_mcp_server, "_CLI_ASYNC_STARTUP_CHECK_SEC", 0.2):
                        running_async = agy_mcp_server.tool_agy_dispatch_task(
                            prompt="slow-run",
                            workspace=str(ws_dir),
                            mode="cli",
                            wait=False,
                        )
                    self.assertTrue(running_async["ok"])
                    self.assertEqual(running_async["status"], "RUNNING")
                    time.sleep(0.9)


if __name__ == "__main__":
    unittest.main()
