import base64
import hashlib
import json
import os
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills/verdantflare-video/scripts"))
import video_client as client

REF = {key: f"01900000-0000-7000-8000-{index:012d}" for index, key in enumerate(
    ("store_id", "artifact_id", "version_id"), 1)}
PAYLOAD = b"\x89PNG\r\n\x1a\nfixture"
DIGEST = hashlib.sha256(PAYLOAD).hexdigest()
ARTIFACT = "art_" + "a" * 32
TASK = "video_task_" + "b" * 32


class Gateway(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def send_json(self, body):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(body).encode())

    def do_POST(self):
        self.server.calls.append(("POST", self.path))
        if self.headers.get("Authorization") != "Bearer test-session":
            self.send_error(403)
            return
        rpc = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if rpc["method"] == "tools/list":
            tools = ["video.create", "video.status", "video.result", "video.import", "video.capabilities", "artifact.read"]
            result = {"tools": [{"name": name} for name in tools]}
        else:
            name, args = rpc["params"]["name"], rpc["params"]["arguments"]
            self.server.tool_calls.append((name, args))
            if name == "video.capabilities":
                data = {"default_route": "h3-sol", "routes": [{"route": "h3-sol", "configured": True,
                    "duration_seconds": {"minimum": 5, "maximum": 15}, "aspect_ratios": ["9:16"]}]}
            elif name == "artifact.read":
                data = {"version": {**REF, "mime": "image/png", "size": len(PAYLOAD), "sha256": DIGEST},
                        "content_path": self.server.content_path}
            elif name == "video.import":
                content = base64.b64decode(args["content_base64"])
                data = {"artifact": {"artifact_id": ARTIFACT, "project_id": args["project_id"],
                                     "sha256": hashlib.sha256(content).hexdigest(), "size": len(content)}}
            elif name == "video.create":
                data = {"task_id": TASK, "status": "queued"}
            else:
                data = {"task_id": TASK, "status": "succeeded"}
            result = {"structuredContent": data}
        self.send_json({"jsonrpc": "2.0", "id": rpc["id"], "result": result})

    def do_GET(self):
        self.server.calls.append(("GET", self.path))
        if self.headers.get("Authorization") != "Bearer test-session" or self.server.deny_download:
            self.send_error(403)
            return
        self.send_response(200)
        self.end_headers()
        self.wfile.write(self.server.payload)


class VideoClientContractTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Gateway)
        self.server.calls, self.server.tool_calls = [], []
        self.server.content_path = f"/v2/artifacts/{REF['version_id']}/content?store_id={REF['store_id']}&artifact_id={REF['artifact_id']}"
        self.server.payload, self.server.deny_download = PAYLOAD, False
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop)
        self.config = client.MCPConfig(f"http://127.0.0.1:{self.server.server_port}/mcp", "test-session", self.root)

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def generate_args(self, *extra):
        return client.make_parser().parse_args(["generate", "--project-id", "demo", "--idempotency-key", "shot/v1",
            "--prompt", "A single shot", "--duration", "15", "--ratio", "9:16", "--image-ref", ARTIFACT + "=identity", *extra])

    def import_args(self):
        path = self.root / "reference.json"
        path.write_text(json.dumps({"content_ref": REF}), encoding="utf-8")
        return client.make_parser().parse_args(["import", "--project-id", "demo", "--content-ref-file", str(path),
                                              "--filename", "reference.png", "--sha256", DIGEST])

    def test_controlled_reference_to_native_import_to_generation(self):
        imported = client.import_mcp_artifact(self.config, self.import_args())
        self.assertEqual(imported["mapping"]["source_content_ref"], REF)
        self.assertEqual(imported["mapping"]["video_artifact_id"], ARTIFACT)
        self.assertEqual(json.loads(Path(imported["mapping_path"]).read_text())["sha256"], DIGEST)
        first = client.generate_mcp(self.config, self.generate_args())
        second = client.generate_mcp(self.config, self.generate_args())
        self.assertEqual(first["task_id"], second["task_id"])
        creates = [args for name, args in self.server.tool_calls if name == "video.create"]
        self.assertEqual(len(creates), 1)
        self.assertEqual(creates[0]["route"], "h3-sol")
        self.assertEqual(creates[0]["duration_seconds"], 15)
        self.assertNotIn("test-session", Path(first["attempt_path"]).read_text())

    def test_unsupported_landscape_rejected_before_submission(self):
        with self.assertRaisesRegex(client.ClientError, "adapter does not accept 15s 16:9"):
            client.generate_mcp(self.config, self.generate_args("--ratio", "16:9"))
        self.assertFalse(any(name == "video.create" for name, _ in self.server.tool_calls))
        self.assertFalse((self.root / "mcp-attempts").exists())

    def test_revoked_and_corrupt_content_never_imported(self):
        for denied, payload in [(True, PAYLOAD), (False, b"corrupt")]:
            self.server.deny_download, self.server.payload = denied, payload
            with self.subTest(denied=denied), self.assertRaises(client.ClientError):
                client.import_mcp_artifact(self.config, self.import_args())
        self.assertFalse(any(name == "video.import" for name, _ in self.server.tool_calls))

    def test_untrusted_download_paths_never_receive_credentials(self):
        for path in ["https://evil.invalid/file", "//evil.invalid/file", "/mcp?token=bad",
                     self.server.content_path + "&store_id=duplicate"]:
            self.server.content_path = path
            with self.subTest(path=path), self.assertRaises(client.ClientError):
                client.import_mcp_artifact(self.config, self.import_args())
        self.assertFalse(any(method == "GET" for method, _ in self.server.calls))

    def test_changed_and_unknown_attempts_do_not_resubmit(self):
        args = self.generate_args()
        inventory = client.check_mcp_tools(self.config)
        with mock.patch.object(client, "check_mcp_tools", return_value=inventory), mock.patch.object(
            client, "call_mcp_tool", side_effect=client.ClientError("connection lost")
        ) as call:
            with self.assertRaises(client.ClientError):
                client.generate_mcp(self.config, args)
            with self.assertRaisesRegex(client.ClientError, "unresolved"):
                client.generate_mcp(self.config, args)
            args.prompt = "Different shot"
            with self.assertRaisesRegex(client.ClientError, "different inputs"):
                client.generate_mcp(self.config, args)
            self.assertEqual(call.call_count, 1)

    def test_discovery_accepts_old_registered_generate_without_import(self):
        pages = [{"tools": [{"name": "video.status"}], "nextCursor": "page2"},
                 {"tools": [{"name": "video.result"}, {"name": "video.generate"}]}]
        with mock.patch.object(client, "_mcp_json_request", side_effect=pages):
            result = client.check_mcp_tools(self.config)
        self.assertEqual(result["create_tool"], "video.generate")
        self.assertIsNone(result["capabilities"])

    def test_default_generation_is_mcp_and_sd2_cannot_enter_legacy_s3(self):
        self.assertEqual(self.generate_args().model, client.FAL_MODEL)
        with mock.patch.object(sys, "argv", ["video_client.py", "generate", "--model", "verdantflare-sd2", "--prompt", "test"]), \
             mock.patch.object(client, "load_config", side_effect=AssertionError("legacy config accessed")), \
             mock.patch.object(sys, "stderr"):
            self.assertEqual(client.main(), 1)

    def test_large_chunk_import_resumes_after_lost_chunk_and_commit_responses(self):
        payload = b"chunked-media" * 300000
        path = self.root / "large.mp4"
        path.write_bytes(payload)
        digest = hashlib.sha256(payload).hexdigest()
        stored = bytearray()
        identity = "imp_" + "c" * 32
        prepared_keys, offsets = [], []
        committed, lose_chunk, lose_commit = False, True, True
        artifact = {"artifact_id": ARTIFACT, "project_id": "demo", "size": len(payload), "sha256": digest}

        def call(config, name, args):
            nonlocal committed, lose_chunk, lose_commit
            if name == "video.import_prepare":
                prepared_keys.append(args["idempotency_key"])
            elif name == "video.import_chunk":
                self.assertEqual(args["import_id"], identity)
                self.assertEqual(args["offset"], len(stored))
                block = base64.b64decode(args["content_base64"])
                self.assertEqual(hashlib.sha256(block).hexdigest(), args["sha256"])
                offsets.append(args["offset"])
                stored.extend(block)
                if lose_chunk:
                    lose_chunk = False
                    raise client.ClientError("chunk response lost")
            elif name == "video.import_commit":
                self.assertEqual(bytes(stored), payload)
                committed = True
                if lose_commit:
                    lose_commit = False
                    raise client.ClientError("commit response lost")
            else:
                self.fail(name)
            return {"import_id": identity, "status": "committed" if committed else "uploading", "offset": len(stored),
                    "chunk_max_bytes": 512 * 1024, **({"artifact": artifact} if committed else {})}

        args = dict(project_id="demo", filename="large.mp4", digest=digest, path=path, purpose="action")
        with mock.patch.object(client, "call_mcp_tool", side_effect=call):
            for message in ("chunk response lost", "commit response lost"):
                with self.assertRaisesRegex(client.ClientError, message):
                    client._chunk_import(self.config, **args)
            result = client._chunk_import(self.config, **args)
        self.assertEqual(result["artifact"], artifact)
        self.assertEqual(len(set(prepared_keys)), 1)
        self.assertEqual(offsets.count(0), 1)
        self.assertGreater(len(payload), 3 * 1024 * 1024)


if __name__ == "__main__":
    unittest.main()
