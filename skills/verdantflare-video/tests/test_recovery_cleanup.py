import copy
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.request
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
os.environ["VERDANTFLARE_VIDEO_TEST_MODE"] = "1"

from config import build_config, parse_env_text
from video_client import (
    ApiError,
    ClientError,
    Media,
    _ApiRedirectHandler,
    _delete_owned_object,
    _owned_object_record,
    _result_request_headers,
    _ResultRedirectHandler,
    api_request,
    cleanup_record_uploads,
    generate,
    recover_submission_record,
    upload_media,
    validate_external_url,
    verify_bucket_lifecycle,
)


def make_config(root: str):
    values = parse_env_text(
        "VERDANTFLARE_VIDEO_API_KEY=api\n"
        "VERDANTFLARE_VIDEO_S3_ACCESS_KEY=access\n"
        "VERDANTFLARE_VIDEO_S3_SECRET_KEY=secret\n"
        f"VERDANTFLARE_VIDEO_STATE_DIR={root}\n"
    )
    return build_config(values)


class FakeResponse:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, _limit):
        return b'{}'


class RecoveryCleanupTests(unittest.TestCase):
    def test_create_sends_identical_idempotency_headers(self):
        with tempfile.TemporaryDirectory() as root:
            config = make_config(root)
            opener = mock.Mock()
            opener.open.return_value = FakeResponse()
            request_id = "a09f0bbf-9865-4bdb-84d3-2cc044ae7012"
            with mock.patch("video_client._api_opener", return_value=opener):
                api_request(config, "POST", "/videos", payload={"model": "verdantflare-sd2"}, request_id=request_id)
            request = opener.open.call_args.args[0]
            headers = {key.lower(): value for key, value in request.header_items()}
            self.assertEqual(headers["idempotency-key"], request_id)
            self.assertEqual(headers["x-request-id"], request_id)

    def _assert_create_error_recovers_with_get(self, create_error):
        with tempfile.TemporaryDirectory() as root:
            config = make_config(root)
            args = SimpleNamespace(
                prompt="product ad",
                output=root,
                duration=10,
                ratio="16:9",
                image=[], image_url=[], audio=[], audio_url=[], video=[], video_url=[],
                generate_audio=True,
                watermark=False,
            )
            calls = []

            def fake_api(_config, method, path, **kwargs):
                calls.append((method, path, kwargs.get("request_id")))
                if method == "POST":
                    raise create_error
                request_id = path.rsplit("/", 1)[-1]
                return {
                    "client_request_id": request_id,
                    "submission_state": "CONFIRMED",
                    "public_task_id": "task-recovered",
                    "billing_state": "COMMITTED",
                }

            with mock.patch("video_client.api_request", side_effect=fake_api), mock.patch(
                "video_client.poll_task", side_effect=lambda _config, task: task
            ):
                self.assertEqual(generate(config, args), 1)
            self.assertEqual([call[0] for call in calls], ["POST", "GET"])
            self.assertTrue(calls[1][1].startswith("/video-submissions/"))

    def test_create_5xx_recovers_with_get_and_never_posts_twice(self):
        self._assert_create_error_recovers_with_get(ApiError(500, "temporary"))

    def test_create_timeout_and_rate_limit_recover_with_get_and_never_post_twice(self):
        for status in (408, 429):
            with self.subTest(status=status):
                self._assert_create_error_recovers_with_get(ApiError(status, "temporary"))

    def test_create_409_in_progress_recovers_with_get_and_never_posts_twice(self):
        self._assert_create_error_recovers_with_get(
            ApiError(409, "in progress", {"code": "submission_in_progress"})
        )

    def test_api_mutation_redirect_is_never_followed(self):
        handler = _ApiRedirectHandler("api.example.com")
        request = urllib.request.Request(
            "https://api.example.com/v1/videos",
            data=b"{}",
            method="POST",
        )
        with self.assertRaisesRegex(ClientError, "mutation redirected"):
            handler.redirect_request(
                request,
                None,
                307,
                "Temporary Redirect",
                {},
                "https://api.example.com/v1/videos-primary",
            )

    def test_unknown_submission_remains_unknown(self):
        with tempfile.TemporaryDirectory() as root:
            config = make_config(root)
            request_id = "27126d9d-aa79-454a-9281-3caf32bbb02e"
            submission = {
                "schema_version": 1,
                "client_request_id": request_id,
                "submission_state": "PENDING",
                "created_at": "2026-08-05T00:00:00Z",
                "updated_at": "2026-08-05T00:00:00Z",
                "cleanup": {"objects": []},
            }
            with mock.patch(
                "video_client._query_submission_with_retries",
                return_value={
                    "client_request_id": request_id,
                    "submission_state": "UNKNOWN",
                    "billing_state": "RESERVED",
                },
            ):
                recovered, task = recover_submission_record(config, submission)
            self.assertIsNone(task)
            self.assertEqual(recovered["submission_state"], "UNKNOWN")
            self.assertNotIn(recovered["submission_state"], {"queued", "failed"})

    def test_hard_ttl_cleanup_is_exact_owned_object_and_idempotent(self):
        with tempfile.TemporaryDirectory() as root:
            config = make_config(root)
            key = f"{config.object_prefix}/2026-08-01/0123456789abcdef0123456789abcdef.mp4"
            item = _owned_object_record(config, key)
            item["delete_after"] = "2026-08-01T00:00:00Z"
            record = {
                "submission_state": "UNKNOWN",
                "updated_at": "2026-08-01T00:00:00Z",
                "cleanup": {"objects": [item]},
            }
            completed = SimpleNamespace(returncode=0, stderr="")
            with mock.patch("video_client.ensure_mc", return_value=Path(root) / "mc"), mock.patch(
                "video_client.subprocess.run", return_value=completed
            ) as run:
                self.assertTrue(cleanup_record_uploads(config, record))
            command = run.call_args.args[0]
            self.assertEqual(command[1:4], ["rm", "--quiet", "--force"])
            self.assertEqual(command[4], f"vfvideo/{config.s3_bucket}/{key}")
            cleaned = record["cleanup"]["objects"][0]
            self.assertEqual(cleaned["cleanup_state"], "CLEANED")
            self.assertNotIn("key", cleaned)
            with mock.patch("video_client._delete_owned_object") as delete:
                self.assertFalse(cleanup_record_uploads(config, record, force=True))
            delete.assert_not_called()

    def test_cleanup_rejects_unowned_or_tampered_manifest(self):
        with tempfile.TemporaryDirectory() as root:
            config = make_config(root)
            item = {
                "bucket": config.s3_bucket,
                "key": f"{config.object_prefix}/2026-08-01/not-an-owned-object.mp4",
                "ownership_proof": "0" * 64,
            }
            with mock.patch("video_client.ensure_mc") as ensure:
                self.assertFalse(_delete_owned_object(config, item))
            ensure.assert_not_called()

    def test_lifecycle_validation_is_read_only_and_accepts_exact_seven_day_rule(self):
        with tempfile.TemporaryDirectory() as root:
            config = make_config(root)
            response = SimpleNamespace(
                returncode=0,
                stdout=json.dumps(
                    {
                        "status": "success",
                        "config": {
                            "Rules": [
                                {
                                    "Status": "Enabled",
                                    "Filter": {"Prefix": config.object_prefix + "/"},
                                    "Expiration": {"Days": 7},
                                }
                            ]
                        },
                    }
                ),
                stderr="",
            )
            with mock.patch("video_client.ensure_mc", return_value=Path(root) / "mc"), mock.patch(
                "video_client.subprocess.run", return_value=response
            ) as run:
                self.assertEqual(verify_bucket_lifecycle(config), 7)
            command = run.call_args.args[0]
            self.assertEqual(command[1:4], ["ilm", "rule", "ls"])
            self.assertEqual(command[-2:], ["--expiry", "--json"])
            self.assertNotIn("add", command)
            self.assertNotIn("rm", command)

    def test_lifecycle_validation_fails_closed_without_mutating_bucket(self):
        with tempfile.TemporaryDirectory() as root:
            config = make_config(root)
            response = SimpleNamespace(
                returncode=0,
                stdout=json.dumps(
                    {
                        "status": "success",
                        "config": {
                            "Rules": [
                                {
                                    "Status": "Enabled",
                                    "Filter": {"Prefix": config.s3_prefix + "/"},
                                    "Expiration": {"Days": 7},
                                }
                            ]
                        },
                    }
                ),
                stderr="",
            )
            with mock.patch("video_client.ensure_mc", return_value=Path(root) / "mc"), mock.patch(
                "video_client.subprocess.run", return_value=response
            ) as run:
                with self.assertRaisesRegex(ClientError, "requires an enabled lifecycle rule"):
                    verify_bucket_lifecycle(config)
            command = run.call_args.args[0]
            self.assertEqual(command[1:4], ["ilm", "rule", "ls"])
            self.assertEqual(command[-2:], ["--expiry", "--json"])

    def test_upload_manifest_is_persisted_before_mc_cp(self):
        with tempfile.TemporaryDirectory() as root:
            config = make_config(root)
            source = Path(root) / "image.png"
            source.write_bytes(b"\x89PNG\r\n\x1a\nmock")
            media = Media(kind="image", source=str(source), size=source.stat().st_size)
            snapshots = []

            def persist(item):
                snapshots.append(copy.deepcopy(item))

            def run_cp(*_args, **_kwargs):
                self.assertEqual(snapshots[0]["upload_state"], "PENDING")
                self.assertTrue(snapshots[0]["ownership_proof"])
                return SimpleNamespace(returncode=0, stdout="", stderr="")

            with mock.patch("video_client.ensure_mc", return_value=Path(root) / "mc"), mock.patch(
                "video_client.subprocess.run", side_effect=run_cp
            ), mock.patch("video_client._verify_uploaded_media"):
                uploaded = upload_media(config, media, record_upload=persist)
            self.assertIsNotNone(uploaded.object_key)
            self.assertEqual([item["upload_state"] for item in snapshots], ["PENDING", "UPLOADED"])

    def test_local_generation_stops_before_upload_when_lifecycle_is_unverified(self):
        with tempfile.TemporaryDirectory() as root:
            config = make_config(root)
            source = Path(root) / "image.png"
            source.write_bytes(b"\x89PNG\r\n\x1a\nmock")
            args = SimpleNamespace(
                prompt="product ad",
                output=root,
                duration=10,
                ratio="16:9",
                image=[str(source)], image_url=[], audio=[], audio_url=[], video=[], video_url=[],
                generate_audio=True,
                watermark=False,
            )
            with mock.patch(
                "video_client.verify_bucket_lifecycle", side_effect=ClientError("lifecycle missing")
            ), mock.patch("video_client.upload_media") as upload, mock.patch("video_client.api_request") as api:
                with self.assertRaisesRegex(ClientError, "lifecycle missing"):
                    generate(config, args)
            upload.assert_not_called()
            api.assert_not_called()

    def test_result_proxy_auth_is_same_origin_only_and_redirect_is_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            config = make_config(root)
            task_id = "task-public"
            proxy_url = f"{config.api_base_url}/videos/{task_id}/content"
            object_url = "https://objects.example/result.mp4"
            self.assertIn("Authorization", _result_request_headers(config, task_id, proxy_url))
            self.assertNotIn("Authorization", _result_request_headers(config, task_id, object_url))

            request = urllib.request.Request(proxy_url, headers={"Authorization": "Bearer api"})
            with self.assertRaisesRegex(ClientError, "result download redirected"):
                _ResultRedirectHandler(config, task_id).redirect_request(
                    request, None, 302, "Found", {}, object_url
                )

    def test_result_redirect_destination_is_never_reached(self):
        class TargetHandler(BaseHTTPRequestHandler):
            authorization = None

            def log_message(self, *_args):
                return

            def do_GET(self):
                type(self).authorization = self.headers.get("Authorization")
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"ok")

        class RedirectHandler(BaseHTTPRequestHandler):
            target_url = ""
            authorization = None

            def log_message(self, *_args):
                return

            def do_GET(self):
                type(self).authorization = self.headers.get("Authorization")
                self.send_response(302)
                self.send_header("Location", type(self).target_url)
                self.end_headers()

        target = ThreadingHTTPServer(("127.0.0.1", 0), TargetHandler)
        redirect = ThreadingHTTPServer(("127.0.0.1", 0), RedirectHandler)
        TargetHandler.authorization = None
        RedirectHandler.authorization = None
        RedirectHandler.target_url = f"http://127.0.0.1:{target.server_port}/result.mp4"
        threads = [
            threading.Thread(target=target.serve_forever, daemon=True),
            threading.Thread(target=redirect.serve_forever, daemon=True),
        ]
        for thread in threads:
            thread.start()
        try:
            with tempfile.TemporaryDirectory() as root:
                task_id = "task-public"
                config = replace(make_config(root), api_base_url=f"http://127.0.0.1:{redirect.server_port}/v1")
                proxy_url = f"{config.api_base_url}/videos/{task_id}/content"
                self.assertEqual(validate_external_url(proxy_url, result=True), proxy_url)
                request = urllib.request.Request(proxy_url, headers={"Authorization": "Bearer api"})
                opener = urllib.request.build_opener(_ResultRedirectHandler(config, task_id))
                with self.assertRaises(ClientError):
                    opener.open(request, timeout=5)
                self.assertEqual(RedirectHandler.authorization, "Bearer api")
                self.assertIsNone(TargetHandler.authorization)
        finally:
            redirect.shutdown()
            target.shutdown()
            for thread in threads:
                thread.join(timeout=2)
            redirect.server_close()
            target.server_close()


if __name__ == "__main__":
    unittest.main()
