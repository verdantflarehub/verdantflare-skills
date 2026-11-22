import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import image_client


class FlowClientTests(unittest.TestCase):
    def test_generate_routes_to_flow_with_integer_credit_limit(self):
        args = ["image_client", "generate", "--project-id", "0199c6d3-6d00-7a21-8b4c-123456789abc", "--prompt", "square",
                "--engine", "google-flow", "--budget-limit", "2"]
        with patch.object(sys, "argv", args), patch.object(image_client, "load_config", return_value=("/mcp", "")), \
                patch.object(image_client, "is_mcp_endpoint", return_value=True), \
                patch.object(image_client, "call_mcp_tool", return_value={"task_id":"test","status":"queued"}) as call:
            self.assertEqual(image_client.main(), 0)
        tool, body = call.call_args.args[2:4]
        self.assertEqual((tool, body["engine"], body["model"], body["budget_limit"]),
                         ("image.create", "google-flow", "nano2", "2"))
        self.assertNotIn("resolution", body)
        self.assertNotIn("quality", body)

    def test_list_accepts_flow_filter(self):
        with patch.object(sys, "argv", ["image_client", "list", "--engine", "google-flow"]), \
                patch.object(image_client, "cmd_list", return_value=0) as run:
            self.assertEqual(image_client.main(), 0)
        self.assertEqual(run.call_args.args[0].engine, "google-flow")

    def test_google_nano_models_default_to_google_flow_and_support_upscale(self):
        args = ["image_client", "generate", "--project-id", "0199c6d3-6d00-7a21-8b4c-123456789abc", "--prompt", "portrait",
                "--model", "nano-pro", "--upscale", "2k"]
        with patch.object(sys, "argv", args), patch.object(image_client, "load_config", return_value=("/mcp", "")), \
                patch.object(image_client, "is_mcp_endpoint", return_value=True), \
                patch.object(image_client, "call_mcp_tool", return_value={"task_id": "test", "status": "queued"}) as call:
            self.assertEqual(image_client.main(), 0)
        tool, body = call.call_args.args[2:4]
        self.assertEqual((tool, body["engine"], body["model"], body["upscale"]),
                         ("image.create", "google-flow", "nano-pro", "2k"))
