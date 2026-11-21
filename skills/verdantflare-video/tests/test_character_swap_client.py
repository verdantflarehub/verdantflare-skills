from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent))
from test_regenerate_2k_client import h3_local_inventory
import test_video_client_fal
from video_client import character_swap_mcp, make_parser


class CharacterSwapClientTest(unittest.TestCase):
    def test_preflight_submission_and_recovery(self):
        inventory = h3_local_inventory()
        inventory["tools"].append("video.character_swap")
        args = make_parser().parse_args(["character-swap", "--project-id", "dev", "--idempotency-key", "test-l5",
            "--source-artifact-id", "art_" + "b"*32, "--character-image-artifact-id", "art_" + "a"*32,
            "--prompt", "Woman replaces Man", "--character-lora-version", "v1", "--preflight-only"])
        with tempfile.TemporaryDirectory() as temp, patch("video_client.check_mcp_tools", return_value=inventory), patch("video_client.call_mcp_tool") as call:
            cfg = test_video_client_fal.VideoClientFalTests().config(temp)
            call.return_value = {"status": "validated"}
            self.assertEqual(character_swap_mcp(cfg, args)["status"], "validated")
            self.assertEqual(call.call_args.args[1], "video.preflight")
            self.assertNotIn("idempotency_key", call.call_args.args[2])
            args.preflight_only = False
            call.side_effect = [{"status": "validated"}, {"video_task_id": "video_task_" + "a"*32}]
            first = character_swap_mcp(cfg, args)
            self.assertEqual(call.call_args.args[1], "video.character_swap")
            self.assertEqual(call.call_args.args[2]["character_lora_version"], "v1")
            call.reset_mock()
            self.assertEqual(character_swap_mcp(cfg, args)["video_task_id"], first["video_task_id"])
            call.assert_not_called()
