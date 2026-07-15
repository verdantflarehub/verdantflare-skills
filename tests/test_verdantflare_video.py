import json
import os
import sys
import tempfile
import threading
import unittest
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "verdantflare-video" / "scripts"
sys.path.insert(0, str(SCRIPTS))

os.environ["VERDANTFLARE_VIDEO_TEST_MODE"] = "1"

from config import ConfigError, build_config, ensure_mc, parse_env_text
from task_store import list_tasks, load_task, save_task
from video_client import ClientError, build_payload, generate


class VideoHandler(BaseHTTPRequestHandler):
    polls = 0

    def log_message(self, *_args):
        return

    def do_POST(self):
        length = int(self.headers["Content-Length"])
        body = json.loads(self.rfile.read(length))
        self.server.received = body
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"id": "task_test", "status": "queued", "model": body["model"]}).encode())

    def do_GET(self):
        if self.path == "/v1/videos/task_test":
            type(self).polls += 1
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            if type(self).polls == 1:
                payload = {"id": "task_test", "status": "queued", "progress": 0}
            else:
                payload = {
                    "id": "task_test",
                    "status": "completed",
                    "progress": 100,
                    "metadata": {"url": f"http://127.0.0.1:{self.server.server_port}/redirect.mp4", "duration": 10, "ratio": "16:9"},
                }
            self.wfile.write(json.dumps(payload).encode())
            return
        if self.path == "/result.mp4":
            data = b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00fake-mp4-data"
            self.send_response(200)
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        if self.path == "/redirect.mp4":
            self.send_response(302)
            self.send_header("Location", "/result.mp4")
            self.end_headers()
            return
        self.send_response(404)
        self.end_headers()


class RefreshHandler(BaseHTTPRequestHandler):
    polls = 0
    posts = 0

    def log_message(self, *_args):
        return

    def do_POST(self):
        type(self).posts += 1
        length = int(self.headers["Content-Length"])
        self.rfile.read(length)
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"id":"task_refresh","status":"queued"}')

    def do_GET(self):
        if self.path == "/v1/videos/task_refresh":
            type(self).polls += 1
            url = f"http://127.0.0.1:{self.server.server_port}/expired.mp4" if type(self).polls == 1 else f"http://127.0.0.1:{self.server.server_port}/fresh.mp4"
            payload = {"id": "task_refresh", "status": "completed", "progress": 100, "metadata": {"url": url}}
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(payload).encode())
            return
        if self.path == "/expired.mp4":
            self.send_response(403)
            self.end_headers()
            return
        if self.path == "/fresh.mp4":
            data = b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00fresh-mp4-data"
            self.send_response(200)
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        self.send_response(404)
        self.end_headers()


class UnknownPostHandler(BaseHTTPRequestHandler):
    posts = 0

    def log_message(self, *_args):
        return

    def do_POST(self):
        type(self).posts += 1
        self.send_response(500)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"error":{"code":"temporary","message":"try later"}}')


class VideoSkillTests(unittest.TestCase):
    def test_minimal_config_uses_defaults(self):
        values = parse_env_text(
            "VERDANTFLARE_VIDEO_API_KEY=api\n"
            "VERDANTFLARE_VIDEO_S3_ACCESS_KEY=access\n"
            "VERDANTFLARE_VIDEO_S3_SECRET_KEY=secret\n"
        )
        with tempfile.TemporaryDirectory() as root:
            values["VERDANTFLARE_VIDEO_STATE_DIR"] = root
            config = build_config(values)
            self.assertEqual(config.api_base_url, "https://api.verdantflarehub.com/v1")
            self.assertEqual(config.poll_interval, 8)
            self.assertTrue(config.upload_prefix.startswith("vscode/verdantflare/input/ak-"))

    def test_config_rejects_duplicate_and_unknown_fields(self):
        with self.assertRaises(ConfigError):
            parse_env_text("VERDANTFLARE_VIDEO_API_KEY=a\nVERDANTFLARE_VIDEO_API_KEY=b\n")
        with self.assertRaises(ConfigError):
            parse_env_text("VERDANTFLARE_VIDEO_API_KEY=a\nUNKNOWN=value\n")
        with self.assertRaises(ConfigError):
            parse_env_text("VERDANTFLARE_VIDEO_API_KEY=$(cat /tmp/key)\n")

    def test_task_store_is_atomic_and_lists_valid_records(self):
        values = parse_env_text(
            "VERDANTFLARE_VIDEO_API_KEY=api\n"
            "VERDANTFLARE_VIDEO_S3_ACCESS_KEY=access\n"
            "VERDANTFLARE_VIDEO_S3_SECRET_KEY=secret\n"
        )
        with tempfile.TemporaryDirectory() as root:
            values["VERDANTFLARE_VIDEO_STATE_DIR"] = root
            config = build_config(values)
            config.ensure_dirs()
            save_task(config, {"schema_version": 1, "task_id": "task_test", "status": "queued"})
            self.assertEqual(load_task(config, "task_test")["task_id"], "task_test")
            self.assertEqual(len(list_tasks(config)), 1)
            bad_path = config.tasks_dir / "wrong-name.json"
            bad_path.write_text(json.dumps({"schema_version": 1, "task_id": "task_bad"}), encoding="utf-8")
            self.assertEqual(len(list_tasks(config)), 1)
            self.assertTrue((config.quarantine_dir / bad_path.name).exists())

    def test_payload_is_openai_structured(self):
        payload = build_payload(
            "make a product ad",
            [SimpleNamespace(kind="image", url="https://assets.example/image.png")],
            duration=10,
            ratio="16:9",
            generate_audio=True,
            watermark=False,
        )
        self.assertEqual(payload["model"], "verdantflare-sd2")
        self.assertEqual(payload["messages"][0]["content"][1]["type"], "image_url")
        self.assertNotIn("metadata", payload)

    def test_generate_persists_and_downloads_public_task(self):
        VideoHandler.polls = 0
        server = ThreadingHTTPServer(("127.0.0.1", 0), VideoHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as root:
                values = parse_env_text(
                    "VERDANTFLARE_VIDEO_API_KEY=api\n"
                    "VERDANTFLARE_VIDEO_S3_ACCESS_KEY=access\n"
                    "VERDANTFLARE_VIDEO_S3_SECRET_KEY=secret\n"
                    f"VERDANTFLARE_VIDEO_API_BASE_URL=http://127.0.0.1:{server.server_port}/v1\n"
                    f"VERDANTFLARE_VIDEO_STATE_DIR={root}\n"
                )
                config = replace(build_config(values), poll_interval=0, task_timeout=10)
                args = SimpleNamespace(
                    prompt="make a product ad",
                    output=root,
                    duration=10,
                    ratio="16:9",
                    image=[],
                    image_url=[],
                    audio=[],
                    audio_url=[],
                    video=[],
                    video_url=[],
                    generate_audio=True,
                    watermark=False,
                )
                self.assertEqual(generate(config, args), 0)
                self.assertEqual(server.received["model"], "verdantflare-sd2")
                records = list_tasks(config)
                self.assertEqual(records[0]["status"], "completed")
                self.assertEqual(records[0]["result"]["download_state"], "SUCCEEDED")
                self.assertTrue(Path(records[0]["result"]["local_path"]).exists())
        finally:
            server.shutdown()
            thread.join(timeout=2)
            server.server_close()

    def test_expired_result_refreshes_once_without_creating_again(self):
        RefreshHandler.polls = 0
        RefreshHandler.posts = 0
        server = ThreadingHTTPServer(("127.0.0.1", 0), RefreshHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as root:
                values = parse_env_text(
                    "VERDANTFLARE_VIDEO_API_KEY=api\n"
                    "VERDANTFLARE_VIDEO_S3_ACCESS_KEY=access\n"
                    "VERDANTFLARE_VIDEO_S3_SECRET_KEY=secret\n"
                    f"VERDANTFLARE_VIDEO_API_BASE_URL=http://127.0.0.1:{server.server_port}/v1\n"
                    f"VERDANTFLARE_VIDEO_STATE_DIR={root}\n"
                )
                config = replace(build_config(values), poll_interval=0, task_timeout=10)
                args = SimpleNamespace(
                    prompt="refresh result",
                    output=root,
                    duration=10,
                    ratio="16:9",
                    image=[], image_url=[], audio=[], audio_url=[], video=[], video_url=[],
                    generate_audio=True, watermark=False,
                )
                self.assertEqual(generate(config, args), 0)
                self.assertEqual(RefreshHandler.posts, 1)
                self.assertEqual(RefreshHandler.polls, 2)
                record = list_tasks(config)[0]
                self.assertEqual(record["result"]["download_state"], "SUCCEEDED")
        finally:
            server.shutdown()
            thread.join(timeout=2)
            server.server_close()

    def test_post_5xx_is_unknown_and_not_retried(self):
        UnknownPostHandler.posts = 0
        server = ThreadingHTTPServer(("127.0.0.1", 0), UnknownPostHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as root:
                values = parse_env_text(
                    "VERDANTFLARE_VIDEO_API_KEY=api\n"
                    "VERDANTFLARE_VIDEO_S3_ACCESS_KEY=access\n"
                    "VERDANTFLARE_VIDEO_S3_SECRET_KEY=secret\n"
                    f"VERDANTFLARE_VIDEO_API_BASE_URL=http://127.0.0.1:{server.server_port}/v1\n"
                    f"VERDANTFLARE_VIDEO_STATE_DIR={root}\n"
                )
                config = build_config(values)
                args = SimpleNamespace(
                    prompt="unknown post",
                    output=root,
                    duration=10,
                    ratio="16:9",
                    image=[], image_url=[], audio=[], audio_url=[], video=[], video_url=[],
                    generate_audio=True, watermark=False,
                )
                with self.assertRaises(ClientError):
                    generate(config, args)
                self.assertEqual(UnknownPostHandler.posts, 1)
                submissions = list(config.submissions_dir.glob("*.json"))
                self.assertEqual(len(submissions), 1)
                self.assertEqual(json.loads(submissions[0].read_text())["submission_state"], "UNKNOWN")
        finally:
            server.shutdown()
            thread.join(timeout=2)
            server.server_close()

    def test_missing_mc_cache_does_not_create_unverified_binary(self):
        values = parse_env_text(
            "VERDANTFLARE_VIDEO_API_KEY=api\n"
            "VERDANTFLARE_VIDEO_S3_ACCESS_KEY=access\n"
            "VERDANTFLARE_VIDEO_S3_SECRET_KEY=secret\n"
        )
        with tempfile.TemporaryDirectory() as root:
            values["VERDANTFLARE_VIDEO_STATE_DIR"] = root
            config = build_config(values)
            with mock.patch("config.platform.machine", return_value="arm64"), mock.patch(
                "config._download", side_effect=ConfigError("network unavailable")
            ):
                with self.assertRaises(ConfigError):
                    ensure_mc(config)
            self.assertFalse(config.mc_path.exists())


if __name__ == "__main__":
    unittest.main()
