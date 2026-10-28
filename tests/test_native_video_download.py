import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills/verdantflare-video/scripts"))
import video_client as client

DATA = b"synthetic decoded video"
ARTIFACT = "art_" + "a" * 32
PATH = f"/artifacts/{ARTIFACT}/content"


class NativeMediaGateway(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def do_GET(self):
        self.server.calls.append((self.path, self.headers.get("Authorization")))
        if self.server.status != 200:
            self.send_response(self.server.status)
            if self.server.status == 302:
                self.send_header("Location", "/unexpected-redirect")
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", self.server.mime)
        self.send_header("Content-Length", str(len(self.server.payload)))
        self.end_headers()
        self.wfile.write(self.server.payload)


class NativeVideoDownloadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), NativeMediaGateway)
        self.server.calls, self.server.status = [], 200
        self.server.payload, self.server.mime = DATA, "video/mp4"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop)
        self.config = client.MCPConfig(f"http://127.0.0.1:{self.server.server_port}/mcp", "test-session", self.root)
        env = patch.dict(os.environ, {"VERDANTFLARE_VIDEO_TEST_MODE": "1"})
        env.start()
        self.addCleanup(env.stop)
        self.result = {"status": "succeeded", "video_task_id": "video_task_" + "b" * 32,
            "project_id": "project-test", "artifact_id": ARTIFACT, "download_path": PATH,
            "media": {"sha256": hashlib.sha256(DATA).hexdigest(), "size": len(DATA)}}
        self.output = self.root / "output.mp4"

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def download(self):
        return client.download_mcp_result(self.config, self.result, str(self.output))

    def test_same_origin_auth_digest_decode_and_reauthorization_on_reuse(self):
        with patch.object(client, "_validate_downloaded_video") as decode:
            self.assertTrue(self.download()["download"]["verified"])
            self.assertTrue(self.download()["download"]["verified"])
            self.assertEqual(decode.call_count, 2)
        self.assertEqual(self.output.read_bytes(), DATA)
        self.assertEqual(self.server.calls, [(PATH, "Bearer test-session")] * 2)
        self.server.status = 403
        with self.assertRaisesRegex(client.ClientError, "HTTP 403"):
            self.download()
        self.assertEqual(self.output.read_bytes(), DATA)

    def test_result_cannot_select_external_or_mismatched_download_path(self):
        for path in ("https://other.invalid" + PATH, PATH + "?token=secret", "/other", "//other.invalid" + PATH):
            with self.subTest(path=path):
                self.result["download_path"] = path
                with self.assertRaisesRegex(client.ClientError, "does not match"):
                    self.download()
        self.assertEqual(self.server.calls, [])
        self.assertFalse(self.output.exists())

    def test_redirect_never_receives_token(self):
        self.server.status = 302
        with self.assertRaisesRegex(client.ClientError, "refusing to forward credentials"):
            self.download()
        self.assertEqual(self.server.calls, [(PATH, "Bearer test-session")])
        self.assertFalse(self.output.exists())

    def test_integrity_and_media_failures_never_publish(self):
        for change in ("digest", "length", "mime", "decode"):
            with self.subTest(change=change):
                self.server.payload, self.server.mime = DATA, "video/mp4"
                self.result["media"]["sha256"] = hashlib.sha256(DATA).hexdigest()
                if change == "digest":
                    self.result["media"]["sha256"] = "f" * 64
                elif change == "length":
                    self.server.payload = DATA + b"extra"
                elif change == "mime":
                    self.server.mime = "text/html"
                with patch.object(client, "_validate_downloaded_video", side_effect=client.ClientError("invalid video")):
                    with self.assertRaises(client.ClientError):
                        self.download()
                self.assertFalse(self.output.exists())

    def test_existing_different_file_is_preserved(self):
        self.output.write_bytes(b"user original")
        with patch.object(client, "_validate_downloaded_video"):
            with self.assertRaisesRegex(client.ClientError, "different content"):
                self.download()
        self.assertEqual(self.output.read_bytes(), b"user original")


class ErrorStatusTests(unittest.TestCase):
    def test_structured_error_retains_safe_status_and_acceptance(self):
        error = {"code": "account_unavailable", "message": "Account cannot accept requests",
                 "source": "google", "accepted": None, "retryable": False, "http_status": 503}
        self.assertEqual(client.MCPToolError(error).error, error)
        for value in (True, "503", 99, 600):
            self.assertNotIn("http_status", client.safe_mcp_error({**error, "http_status": value}))
