import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills/verdantflare-video/scripts"))
import video_client as client


class DraftReferenceTest(unittest.TestCase):
    def test_draft_uses_reference_preflight_and_recovers_one_create(self):
        inventory = {"tools": ["video.preflight"], "create_tool": "video.create", "capabilities": {"routes": [{
            "route": "higgsfield", "model": "seedance_2_5_draft", "enabled": True, "configured": True,
            "duration_seconds": {"minimum": 4, "maximum": 15}, "aspect_ratios": ["9:16"],
            "modes": ["reference_to_video"], "resolutions": ["480p"],
            "reference_limits": {"images": 4, "videos": 1, "audios": 0}}]}}
        args = client.make_parser().parse_args(["generate", "--project-id", "demo", "--idempotency-key", "draft/v1",
            "--prompt", "An adult dancer", "--duration", "4", "--ratio", "9:16", "--route", "higgsfield",
            "--model", "seedance_2_5_draft", "--image-ref", "art_" + "a" * 32 + "=identity",
            "--video-ref", "art_" + "b" * 32 + "=motion"])
        with tempfile.TemporaryDirectory() as root:
            config = client.MCPConfig("https://studio.invalid/mcp", "synthetic", Path(root))
            with mock.patch.object(client, "check_mcp_tools", return_value=inventory), mock.patch.object(
                client, "call_mcp_tool", side_effect=[{"quoted_credits": 12}, {"video_task_id": "video_task_" + "c" * 32}]) as call:
                client.generate_mcp(config, args)
                client.generate_mcp(config, args)
                self.assertEqual([c.args[1] for c in call.call_args_list], ["video.preflight", "video.create"])
                request = call.call_args_list[-1].args[2]
                self.assertEqual(request["mode"], "reference_to_video")
                self.assertEqual(request["model"], "seedance_2_5_draft")
                self.assertEqual(request["resolution"], "480p")
                self.assertEqual(len(request["references"]["images"]), 1)
                self.assertEqual(len(request["references"]["videos"]), 1)
