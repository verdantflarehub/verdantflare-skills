import io
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import video_client as client
from mcp_fixture import fal_inventory


class DynamicChannelTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.config = client.MCPConfig("https://studio.invalid/mcp", "test-token", Path(self.temp.name))
        self.inventory = fal_inventory()
        self.inventory["tools"].append("video.preflight")
        self.discovery = patch.object(client, "check_mcp_tools", return_value=self.inventory)
        self.discovery.start()
        self.addCleanup(self.discovery.stop)

    def args(self, *extra):
        return client.make_parser().parse_args(["generate", "--project-id", "demo", "--idempotency-key", "shot/v1", "--prompt", "boat", *extra])

    def submit(self, args):
        with patch.object(client, "call_mcp_tool", side_effect=[{"can_submit": True}, {"video_task_id": "video_task_" + "a" * 32}]) as calls:
            client.generate_mcp(self.config, args)
            self.assertEqual([c.args[1] for c in calls.call_args_list], ["video.preflight", "video.generate"])
            return calls.call_args_list[-1].args[2]

    def test_fal_sd2_selects_second_model_and_sends_exact_budget(self):
        self.inventory["capabilities"]["routes"].append({"route": "fal", "model": "bytedance-seedance-2.0-reference-to-video",
            "configured": True, "duration_seconds": {"minimum": 4, "maximum": 15}, "aspect_ratios": ["21:9"],
            "resolutions": ["720p"], "reference_limits": {"images": 2, "videos": 1, "audios": 1}})
        request = self.submit(self.args("--route", "fal", "--model", "bytedance-seedance-2.0-reference-to-video",
            "--duration", "4", "--ratio", "21:9", "--resolution", "720p", "--image-ref", "art_1=identity",
            "--budget-limit", "1.25", "--channel-account-id", "account_test"))
        self.assertEqual(request["aspect_ratio"], "21:9")
        self.assertEqual(request["budget_limit"], "1.25")
        self.assertEqual(request["channel_account_id"], "account_test")

    def google(self):
        self.inventory["capabilities"]["routes"] = [{"route": "google", "model": "omni-stable",
            "models": ["omni-preview"], "configured": True, "enabled": True,
            "modes": ["text_to_video", "image_to_video"], "duration_seconds": {"values": [4, 6, 8]},
            "aspect_ratios": ["16:9", "9:16"], "resolutions": ["720p", "1080p"],
            "reference_limits": {"images": 1, "videos": 0, "audios": 0}}]

    def test_google_alias_and_1080p_come_from_capabilities(self):
        self.google()
        request = self.submit(self.args("--route", "google", "--model", "omni-preview", "--duration", "8",
            "--ratio", "16:9", "--resolution", "1080p"))
        self.assertEqual(request["model"], "omni-preview")
        self.assertEqual(request["mode"], "text_to_video")

    def test_google_image_ratio_is_left_to_service_without_explicit_choice(self):
        self.google()
        request = self.submit(self.args("--route", "google", "--model", "omni-preview", "--duration", "4",
            "--resolution", "720p", "--image-ref", "art_1=first_frame"))
        self.assertEqual(request["mode"], "image_to_video")
        self.assertNotIn("aspect_ratio", request)

    def test_declared_counts_and_schema_conflicts_do_not_submit(self):
        self.google()
        for extra in (["--image-ref", "a=identity", "--image-ref", "b=identity"], ["--resolution", "1080p"]):
            self.inventory["schemas"]["video.generate"]["properties"]["resolution"] = {"type": "string", "enum": ["720p"]}
            with self.subTest(extra=extra), patch.object(client, "call_mcp_tool") as calls, self.assertRaises(client.ClientError):
                client.generate_mcp(self.config, self.args("--route", "google", "--model", "omni-preview", "--duration", "4", *extra))
            calls.assert_not_called()

    def test_missing_and_ambiguous_capabilities_do_not_guess(self):
        rows = self.inventory["capabilities"]["routes"]
        rows.append(dict(rows[0]))
        with patch.object(client, "call_mcp_tool") as calls, self.assertRaises(client.ClientError):
            client.generate_mcp(self.config, self.args("--image-ref", "a=identity"))
        calls.assert_not_called()

    def test_preflight_false_never_creates_or_saves_submitting(self):
        with patch.object(client, "call_mcp_tool", return_value={"can_submit": False, "blocked_reason": "quota_exhausted"}) as calls:
            with self.assertRaises(client.MCPToolError) as raised:
                client.generate_mcp(self.config, self.args("--image-ref", "a=identity"))
        self.assertEqual(raised.exception.error["code"], "quota_exhausted")
        self.assertFalse(raised.exception.error["accepted"])
        self.assertEqual(calls.call_count, 1)
        self.assertFalse((self.config.state_dir / "mcp-attempts").exists())

    def test_budget_field_absent_from_schema_stops_before_preflight(self):
        self.inventory["schemas"]["video.generate"]["properties"].pop("budget_limit")
        with patch.object(client, "call_mcp_tool") as calls, self.assertRaisesRegex(client.ClientError, "budget_limit"):
            client.generate_mcp(self.config, self.args("--image-ref", "a=identity", "--budget-limit", "3"))
        calls.assert_not_called()

    def test_higgsfield_credit_ceiling_keeps_its_unit(self):
        self.inventory["capabilities"]["routes"] = [{"route": "higgsfield", "model": "seedance_2_5_draft",
            "configured": True, "enabled": True, "modes": ["reference_to_video"], "resolutions": ["480p"],
            "duration_seconds": {"minimum": 4, "maximum": 15}, "aspect_ratios": ["16:9"]}]
        request = self.submit(self.args("--route", "higgsfield", "--model", "seedance_2_5_draft", "--image-ref", "a=identity", "--price-ceiling", "24"))
        self.assertEqual(request["price_ceiling"], 24)
        self.assertNotIn("budget_limit", request)

    def test_unknown_submission_keeps_original_attempt(self):
        error = client.MCPToolError({"code": "provider_timeout", "message": "provider timeout", "source": "fal", "retryable": True, "accepted": None})
        with patch.object(client, "call_mcp_tool", side_effect=[{"can_submit": True}, error]) as calls:
            args = self.args("--image-ref", "a=identity")
            with self.assertRaises(client.MCPToolError):
                client.generate_mcp(self.config, args)
            with self.assertRaisesRegex(client.ClientError, "unresolved"):
                client.generate_mcp(self.config, args)
        self.assertEqual(calls.call_count, 2)
        record = json.loads(next((self.config.state_dir / "mcp-attempts").glob("*.json")).read_text())
        self.assertEqual(record["state"], "submission_unknown")
        self.assertIsNone(record["error"]["accepted"])

    def test_smaller_declared_import_limit_stops_before_upload(self):
        source = self.config.state_dir / "source.mp4"
        source.write_bytes(b"synthetic media")
        with patch.object(client, "call_mcp_tool") as calls, self.assertRaisesRegex(client.ClientError, "transport limit"):
            client._chunk_import(self.config, project_id="demo", filename="source.mp4", path=source,
                digest=hashlib.sha256(source.read_bytes()).hexdigest(), purpose="motion", limits={"max_file_bytes": 2})
        calls.assert_not_called()


class ErrorPropagationTests(unittest.TestCase):
    def test_structured_and_text_error_keep_safe_specific_reason(self):
        for structured in (True, False):
            error = {"code": "limits_unavailable", "message": "Credits unavailable https://internal.invalid/a?signature=secret Bearer secret C:\\private\\key.json",
                "source": "higgsfield", "retryable": True, "accepted": False}
            payload = {"error": error}
            result = {"isError": True, "content": [{"type": "text", "text": json.dumps(payload)}]}
            if structured:
                result["structuredContent"] = payload
            with self.subTest(structured=structured), patch.object(client, "_mcp_json_request", return_value=result), self.assertRaises(client.MCPToolError) as raised:
                client.call_mcp_tool(None, "video.create", {})
            diagnostic = raised.exception.error
            self.assertEqual(diagnostic["code"], "limits_unavailable")
            self.assertTrue(diagnostic["retryable"])
            self.assertFalse(diagnostic["accepted"])
            self.assertNotIn("secret", diagnostic["message"])
            self.assertNotIn("internal.invalid", diagnostic["message"])
            self.assertNotIn("private", diagnostic["message"])

    def test_cli_emits_structured_safe_error(self):
        error = client.MCPToolError({"code": "model_retired", "message": "Selected model is retired", "source": "google", "retryable": False, "accepted": False})
        with patch.object(sys, "argv", ["video_client.py", "check"]), patch.object(client, "load_mcp_config"), patch.object(client, "check_mcp_tools", side_effect=error), patch.object(sys, "stderr", new_callable=io.StringIO) as output:
            self.assertEqual(client.main(), 1)
        self.assertEqual(json.loads(output.getvalue())["error"]["code"], "model_retired")

    def test_conflicting_error_envelopes_fail_without_leaking_raw_text(self):
        with patch.object(client, "_mcp_json_request", return_value={"isError": True,
            "structuredContent": {"error": {"code": "authorization_required", "message": "authorization required"}},
            "content": [{"type": "text", "text": json.dumps({"error": {"code": "other"}})}]}), self.assertRaises(client.ProtocolError):
            client.call_mcp_tool(None, "video.create", {})
