#!/usr/bin/env python3
"""verdantflare-image 技能与客户端自动化测试套件。"""

import argparse
import hashlib
import json
import os
import sys
import tempfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
import unittest

ROOT_DIR = Path(__file__).resolve().parents[1]
SKILL_DIR = ROOT_DIR / "skills" / "verdantflare-image"
SCRIPTS_DIR = SKILL_DIR / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import image_client


class MockImageServerHandler(BaseHTTPRequestHandler):
    """模拟 Image MCP 服务端行为。"""

    poll_count = 0
    received_tasks = []

    def log_message(self, format, *args):
        # 静音标准 HTTP 请求日志
        return

    def do_GET(self):
        auth = self.headers.get("Authorization", "")
        if auth != "Bearer test-valid-token":
            self.send_response(401)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"error": "unauthorized"}')
            return

        if self.path.startswith("/api/tasks/stats"):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"total": 1, "queued": 0, "running": 0, "completed": 1, "failed": 0}')
            return

        if self.path.startswith("/api/tasks"):
            MockImageServerHandler.poll_count += 1
            status = "queued" if MockImageServerHandler.poll_count == 1 else "completed"
            artifact_id = "art-test-mock-001" if status == "completed" else None
            resp_data = {
                "tasks": [
                    {
                        "task_id": "img-task-mock-01",
                        "project_id": "test-project",
                        "status": status,
                        "engine": "gemini",
                        "model": "gemini-3.1-flash-image",
                        "prompt_preview": "test prompt",
                        "duration_seconds": 3.5,
                        "artifact_id": artifact_id,
                    }
                ],
                "total": 1,
            }
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(resp_data).encode("utf-8"))
            return

        if self.path.startswith("/artifacts/art-test-mock-001/content"):
            content = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDRmock_image_bytes"
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.end_headers()
            self.wfile.write(content)
            return

        self.send_response(404)
        self.end_headers()

    def do_POST(self):
        auth = self.headers.get("Authorization", "")
        if auth != "Bearer test-valid-token":
            self.send_response(401)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"error": "unauthorized"}')
            return

        if self.path.startswith("/api/tasks"):
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length).decode("utf-8"))
            MockImageServerHandler.received_tasks.append(body)

            self.send_response(201)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            resp = {
                "task_id": "img-task-mock-01",
                "status": "queued",
                "project_id": body.get("project_id", "default"),
                "created_at": "2026-09-09T12:00:00Z",
            }
            self.wfile.write(json.dumps(resp).encode("utf-8"))
            return

        self.send_response(404)
        self.end_headers()


class TestVerdantflareImage(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), MockImageServerHandler)
        cls.port = cls.server.server_port
        cls.server_thread = Thread(target=cls.server.serve_forever, daemon=True)
        cls.server_thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        MockImageServerHandler.poll_count = 0
        MockImageServerHandler.received_tasks = []

    def test_skill_structure(self):
        """测试 Skill 目录与规范文件完整性。"""
        self.assertTrue(SKILL_DIR.is_dir(), f"Skill 目录不存在: {SKILL_DIR}")
        self.assertTrue((SKILL_DIR / "SKILL.md").is_file())
        self.assertTrue((SKILL_DIR / "agents" / "openai.yaml").is_file())
        self.assertTrue((SKILL_DIR / "references" / "contracts.md").is_file())
        self.assertTrue((SKILL_DIR / "references" / "visual-specs.md").is_file())
        self.assertTrue((SKILL_DIR / "references" / "workflow.md").is_file())
        self.assertTrue((SKILL_DIR / "scripts" / "image_client.py").is_file())

        # 确保旧废弃技能已彻底删除
        old_skill = ROOT_DIR / "skills" / "verdantflare-image-codex"
        self.assertFalse(old_skill.exists(), "已废弃的 verdantflare-image-codex 目录不应存在")

    def test_skill_frontmatter(self):
        """检查 SKILL.md Frontmatter 名称正确性。"""
        content = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("name: verdantflare-image", content)
        self.assertNotIn("name: verdantflare-image-codex", content)

    def test_env_parsing(self):
        """测试 .env 文件解析与哈希计算工具。"""
        with tempfile.NamedTemporaryFile("w+", delete=False) as f:
            f.write("# comment\nIMAGE_MCP_URL=https://example.com/image\nIMAGE_MCP_BEARER_TOKEN=\"secret123\"\n")
            f_path = Path(f.name)

        try:
            parsed = image_client.parse_env_file(f_path)
            self.assertEqual(parsed.get("IMAGE_MCP_URL"), "https://example.com/image")
            self.assertEqual(parsed.get("IMAGE_MCP_BEARER_TOKEN"), "secret123")
        finally:
            f_path.unlink()

        # 测试已知哈希
        data = b""
        self.assertEqual(
            image_client.calculate_sha256(data),
            "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        )

    def test_end_to_end_mock_workflow(self):
        """测试客户端从提交、轮询、下载到哈希校验的全流程闭环。"""
        base_url = f"http://127.0.0.1:{self.port}"
        token = "test-valid-token"

        with tempfile.TemporaryDirectory() as tmp_dir:
            out_file = Path(tmp_dir) / "output.png"
            os.environ["IMAGE_MCP_URL"] = base_url
            os.environ["IMAGE_MCP_BEARER_TOKEN"] = token

            ret = image_client.cmd_generate(
                argparse.Namespace(
                    prompt="test mock prompt",
                    engine="codex",
                    model="",
                    aspect_ratio="16:9",
                    resolution="2k",
                    quality="auto",
                    project_id="test-project",
                    idempotency_key="unit-01",
                    wait=True,
                    timeout=10.0,
                    output=str(out_file),
                )
            )
            self.assertEqual(ret, 0)
            self.assertTrue(out_file.is_file())
            content = out_file.read_bytes()
            expected_sha = hashlib.sha256(content).hexdigest()
            self.assertEqual(image_client.calculate_sha256(content), expected_sha)
            self.assertEqual(len(MockImageServerHandler.received_tasks), 1)
            self.assertEqual(MockImageServerHandler.received_tasks[0]["engine"], "codex")
            self.assertEqual(MockImageServerHandler.received_tasks[0]["model"], "gpt-image-2")


if __name__ == "__main__":
    unittest.main()
