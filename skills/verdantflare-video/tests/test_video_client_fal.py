from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
import urllib.request
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
os.environ["VERDANTFLARE_VIDEO_TEST_MODE"] = "1"

from video_client import (
    ClientError,
    MCPConfig,
    _mcp_json_request,
    _mcp_reference,
    check_mcp_tools,
    generate_mcp,
    make_parser,
    resume_mcp,
)


class FakeResponse:
    status = 200

    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, _limit):
        return json.dumps(self.payload).encode("utf-8")


class VideoClientFalTests(unittest.TestCase):
    def config(self, root: str) -> MCPConfig:
        return MCPConfig("http://127.0.0.1:8000/mcp", "test-token", Path(root))

    def args(self, **updates):
        value = {
            "model": "minimax-h3-ref2va",
            "route": "fal",
            "project_id": "demo",
            "idempotency_key": "shot-001/v1",
            "prompt": "single-shot camera move",
            "duration": 5,
            "ratio": "adaptive",
            "image_ref": ["art_image=identity"],
            "video_ref": [],
            "audio_ref": [],
            "image": [],
            "image_url": [],
            "video": [],
            "video_url": [],
            "audio": [],
            "audio_url": [],
            "output": None,
            "generate_audio": True,
            "watermark": False,
        }
        value.update(updates)
        return SimpleNamespace(**value)

    def test_reference_requires_artifact_and_purpose(self):
        self.assertEqual(_mcp_reference("art_1=character identity"), {"artifact_id": "art_1", "purpose": "character identity"})
        for value in ("art_1", "=identity", "art_1="):
            with self.subTest(value=value), self.assertRaises(ClientError):
                _mcp_reference(value)

    def test_json_rpc_request_keeps_bearer_on_mcp_origin(self):
        config = MCPConfig("http://127.0.0.1:8000/mcp", "test-token", Path("/tmp/state"))
        request_id = "0" * 24
        response = FakeResponse({"jsonrpc": "2.0", "id": request_id, "result": {"tools": []}})
        opener = mock.Mock(spec=urllib.request.OpenerDirector)
        opener.open.return_value = response
        with mock.patch("video_client.os.urandom", return_value=b"fixed"), mock.patch(
            "video_client.hashlib.sha256"
        ) as digest, mock.patch("video_client._mcp_opener", return_value=opener):
            digest.return_value.hexdigest.return_value = request_id
            value = _mcp_json_request(config, "tools/list", {})
        self.assertEqual(value, {"tools": []})
        request = opener.open.call_args.args[0]
        headers = {key.lower(): value for key, value in request.header_items()}
        self.assertEqual(headers["authorization"], "Bearer test-token")
        body = json.loads(request.data)
        self.assertEqual(body["method"], "tools/list")

    def test_check_requires_all_domain_tools(self):
        inventory = {
            "tools": [{"name": name} for name in ("artifact.import", "video.generate", "video.status", "video.result")]
        }
        with mock.patch("video_client._mcp_json_request", return_value=inventory):
            result = check_mcp_tools(self.config("/tmp/state"))
        self.assertEqual(result["route"], "fal")
        self.assertEqual(result["model"], "minimax-h3-ref2va")

    def test_generate_locks_model_route_and_persists_attempt(self):
        with tempfile.TemporaryDirectory() as root:
            calls = []

            def fake_call(_config, name, arguments):
                calls.append((name, arguments))
                return {"video_task_id": "video_task_" + "a" * 32, "status": "queued"}

            with mock.patch("video_client.call_mcp_tool", side_effect=fake_call):
                result = generate_mcp(self.config(root), self.args())
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0][0], "video.generate")
            self.assertEqual(calls[0][1]["model"], "minimax-h3-ref2va")
            self.assertEqual(calls[0][1]["route"], "fal")
            self.assertNotIn("resolution", calls[0][1])
            record = json.loads(Path(result["attempt_path"]).read_text(encoding="utf-8"))
            self.assertEqual(record["state"], "confirmed")
            self.assertEqual(record["video_task_id"], result["video_task_id"])

    def test_unified_parser_selects_fal_model_and_route(self):
        args = make_parser().parse_args(
            [
                "generate",
                "--model",
                "minimax-h3-ref2va",
                "--route",
                "fal",
                "--project-id",
                "demo",
                "--idempotency-key",
                "shot-001/v1",
                "--prompt",
                "single-shot camera move",
                "--image-ref",
                "art_image=identity",
            ]
        )
        self.assertEqual((args.model, args.route, args.project_id), ("minimax-h3-ref2va", "fal", "demo"))
        self.assertIsNone(args.duration)
        self.assertIsNone(args.ratio)

    def test_fal_defaults_are_locked_inside_unified_client(self):
        with tempfile.TemporaryDirectory() as root, mock.patch(
            "video_client.call_mcp_tool",
            return_value={"video_task_id": "video_task_" + "c" * 32, "status": "queued"},
        ) as call:
            generate_mcp(self.config(root), self.args(duration=None, ratio=None))
        request = call.call_args.args[2]
        self.assertEqual(request["duration_seconds"], 5)
        self.assertEqual(request["aspect_ratio"], "adaptive")
        self.assertEqual(request["route"], "fal")

    def test_fal_requires_exact_model_route_pair(self):
        with tempfile.TemporaryDirectory() as root, mock.patch("video_client.call_mcp_tool") as call:
            with self.assertRaisesRegex(ClientError, "requires --model"):
                generate_mcp(self.config(root), self.args(route=None))
        call.assert_not_called()

    def test_generate_failure_is_unknown_and_never_falls_back(self):
        with tempfile.TemporaryDirectory() as root:
            with mock.patch("video_client.call_mcp_tool", side_effect=ClientError("unavailable")) as call:
                with self.assertRaises(ClientError):
                    generate_mcp(self.config(root), self.args(audio_ref=["art_audio=rhythm"], image_ref=[]))
            self.assertEqual(call.call_count, 1)
            record_path = next((Path(root) / "mcp-attempts").glob("*.json"))
            record = json.loads(record_path.read_text(encoding="utf-8"))
            self.assertEqual(record["state"], "submission_unknown")
            self.assertEqual(record["request"]["route"], "fal")

    def test_reference_limits_are_checked_before_network(self):
        with tempfile.TemporaryDirectory() as root:
            too_many = [f"art_{index}=identity" for index in range(10)]
            with mock.patch("video_client.call_mcp_tool") as call, self.assertRaises(ClientError):
                generate_mcp(self.config(root), self.args(image_ref=too_many))
            call.assert_not_called()

    def test_resume_uses_same_task_and_returns_result(self):
        task_id = "video_task_" + "b" * 32
        with tempfile.TemporaryDirectory() as root, mock.patch(
            "video_client.mcp_status", side_effect=[{"status": "queued"}, {"status": "succeeded"}]
        ) as status_call, mock.patch(
            "video_client.mcp_result", return_value={"video_task_id": task_id, "artifact_id": "art_result"}
        ) as result_call, mock.patch("video_client.time.sleep"):
            value = resume_mcp(self.config(root), task_id, 1, 10)
        self.assertEqual(value["artifact_id"], "art_result")
        self.assertEqual(status_call.call_count, 2)
        result_call.assert_called_once_with(mock.ANY, task_id)


if __name__ == "__main__":
    unittest.main()
