import importlib.util
import json
import hashlib
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / "skills/verdantflare-video/scripts"
sys.path.insert(0, str(SCRIPTS))
import video_client as client
import higgsfield as admin


class HiggsfieldSkillTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.config = client.MCPConfig("https://studio.example.invalid/mcp", "synthetic-session", Path(self.temp.name))
        self.calls = []
        self.inventory = {"create_tool": "video.create", "tools": ["video.create", "video.preflight"], "capabilities": {
            "routes": [{"route": "higgsfield", "model": "seedance_2_5", "configured": True, "enabled": True,
                        "duration_seconds": {"minimum": 4, "maximum": 15}, "aspect_ratios": ["16:9"],
                        "resolutions": ["720p"], "modes": ["text_to_video"]},
                       {"route": "higgsfield", "model": "grok_video_v15_lite", "configured": True, "enabled": True,
                        "duration_seconds": {"minimum": 2, "maximum": 15}, "aspect_ratios": ["16:9"],
                        "resolutions": ["480p", "720p"], "modes": ["text_to_video", "image_to_video"]}]}}

    def tearDown(self):
        self.temp.cleanup()

    def args(self, *extra):
        return client.make_parser().parse_args(["generate", "--route", "higgsfield", "--project-id", "project-test",
            "--idempotency-key", "attempt", "--prompt", "boat", *extra])

    def invoke(self, args):
        def call(_config, name, payload):
            self.calls.append((name, payload))
            return {"video_task_id": "video_task_" + "1" * 32, "status": "queued"} if name == "video.create" else {"status": "validated"}
        with patch.object(client, "check_mcp_tools", return_value=self.inventory), patch.object(client, "call_mcp_tool", side_effect=call):
            return client.generate_mcp(self.config, args)

    def test_selects_capability_by_model_as_well_as_route(self):
        self.invoke(self.args("--model", "grok_video_v15_lite", "--duration", "2", "--resolution", "480p"))
        self.assertEqual([name for name, _ in self.calls], ["video.preflight", "video.create"])
        payload = self.calls[-1][1]
        self.assertEqual(payload["model"], "grok_video_v15_lite")
        self.assertEqual(payload["route"], "higgsfield")
        self.assertEqual(payload["references"], {"images": [], "videos": [], "audios": []})
        self.assertNotIn("quality_profile", payload)

    def test_missing_model_never_preflights_or_submits(self):
        with self.assertRaisesRegex(client.ClientError, "explicitly select --model"):
            self.invoke(self.args("--duration", "4"))
        self.assertEqual(self.calls, [])

    def test_explicit_model_and_existing_attempt_are_preserved(self):
        args = self.args("--model", "seedance_2_5", "--duration", "4")
        first = self.invoke(args)
        second = self.invoke(args)
        self.assertEqual(first["video_task_id"], second["video_task_id"])
        self.assertEqual(self.calls[-1][1]["model"], "seedance_2_5")
        self.assertEqual(sum(name == "video.create" for name, _ in self.calls), 1)

    def test_disabled_or_unsupported_combination_never_submits(self):
        for args in (self.args("--model", "seedance_2_5", "--duration", "2"), self.args("--model", "seedance_2_5", "--duration", "4", "--resolution", "480p")):
            with self.assertRaises(client.ClientError):
                self.invoke(args)
        self.inventory["capabilities"]["routes"][0]["enabled"] = False
        with self.assertRaises(client.ClientError):
            self.invoke(self.args("--model", "seedance_2_5", "--duration", "4"))
        self.assertEqual(self.calls, [])

    def test_admin_state_cannot_refresh_after_handover(self):
        state = Path(self.temp.name)
        (state / "server-owned.json").write_text('{"revision":"synthetic"}')
        with patch.object(admin, "STATE", state), patch.object(admin.subprocess, "run") as run:
            with self.assertRaisesRegex(admin.AdminError, "credentials_owned_by_server"):
                admin.run_cli(["account", "status"], "http://127.0.0.1:19081")
        run.assert_not_called()

    def test_native_download_verifies_and_preserves_existing_content(self):
        data = b"synthetic validated video"
        result = {"status": "succeeded", "content_ref": {"fixed": "reference"},
                  "media": {"size": len(data), "sha256": hashlib.sha256(data).hexdigest()}}
        output = Path(self.temp.name) / "output.mp4"
        def download(_config, _result, staged):
            staged.write_bytes(data)
        with patch.object(client, "_download_content_ref", side_effect=download), \
             patch.object(client, "_validate_downloaded_video"):
            self.assertTrue(client.download_mcp_result(self.config, result, str(output))["download"]["verified"])
            self.assertTrue(client.download_mcp_result(self.config, result, str(output))["download"]["verified"])
            output.write_bytes(b"user original")
            with self.assertRaisesRegex(client.ClientError, "different content"):
                client.download_mcp_result(self.config, result, str(output))
            self.assertEqual(output.read_bytes(), b"user original")

    def test_native_download_mismatched_video_digest_never_publishes(self):
        output = Path(self.temp.name) / "output.mp4"
        result = {"status": "succeeded", "content_ref": {"fixed": "reference"},
                  "media": {"size": 5, "sha256": "0" * 64}}
        def download(_config, _result, staged):
            staged.write_bytes(b"other")
        with patch.object(client, "_download_content_ref", side_effect=download):
            with self.assertRaisesRegex(client.ClientError, "does not match"):
                client.download_mcp_result(self.config, result, str(output))
        self.assertFalse(output.exists())

    def test_admin_requires_dmit_and_never_installs_unverified_archive(self):
        with self.assertRaisesRegex(admin.AdminError, "explicit_dmit_proxy_required"):
            admin.environment("http://other.invalid")
        bad = Path(self.temp.name) / "bad.tar.gz"
        bad.write_bytes(b"not an official archive")
        with self.assertRaisesRegex(admin.AdminError, "official_archive_integrity_failed"):
            admin.install(bad)

    def test_unknown_handover_blocks_local_refresh(self):
        state = Path(self.temp.name)
        (state / "credentials.json").write_text('{"access_token":"synthetic","refresh_token":"synthetic"}')
        (state / "config.json").write_text('{}')
        with patch.object(admin, "STATE", state), patch.object(admin.shutil, "which", return_value="kubectl"), \
             patch.object(admin.subprocess, "run", side_effect=admin.subprocess.TimeoutExpired("kubectl", 60)):
            with self.assertRaises(admin.subprocess.TimeoutExpired):
                admin.handover("import-pod", "gateway", "workspace_test")
            self.assertTrue((state / "handover-pending.json").exists())
            with self.assertRaisesRegex(admin.AdminError, "credentials_owned_by_server"):
                admin.run_cli(["account", "status"], "http://127.0.0.1:19081")

    def test_reconcile_confirms_exact_receipt_without_credentials_or_refresh(self):
        state = Path(self.temp.name)
        pending = {"revision": "a" * 64, "workspace_id": "workspace_test"}
        (state / "handover-pending.json").write_text(json.dumps(pending))
        receipt = {"imported": True, **pending}
        with patch.object(admin, "STATE", state), patch.object(admin.shutil, "which", return_value="kubectl"), \
             patch.object(admin.subprocess, "run", return_value=SimpleNamespace(returncode=0, stdout=json.dumps(receipt))) as run:
            self.assertTrue(admin.reconcile("gateway-pod", "gateway")["local_refresh_disabled"])
            self.assertEqual(run.call_args.kwargs["input"], b"")
            self.assertIn("--receipt", run.call_args.args[0])
        self.assertFalse((state / "handover-pending.json").exists())
        self.assertTrue((state / "server-owned.json").exists())

    def test_mismatched_handover_receipt_keeps_refresh_blocked(self):
        state = Path(self.temp.name)
        pending = {"revision": "a" * 64, "workspace_id": "workspace_test"}
        (state / "handover-pending.json").write_text(json.dumps(pending))
        for receipt in ({"imported": True, **pending, "revision": "b" * 64},
                        {"imported": True, **pending, "workspace_id": "other"}):
            with patch.object(admin, "STATE", state):
                with self.assertRaisesRegex(admin.AdminError, "credential_handover_unconfirmed"):
                    admin.confirm_handover(receipt, pending)
        self.assertTrue((state / "handover-pending.json").exists())


if __name__ == "__main__":
    unittest.main()
