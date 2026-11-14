from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

TESTS_DIR = Path(__file__).resolve().parent
SCRIPTS_DIR = TESTS_DIR.parent / "scripts"
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))
os.environ["VERDANTFLARE_VIDEO_TEST_MODE"] = "1"

import test_video_client_fal
from mcp_fixture import schemas
from video_client import (
    ClientError,
    download_mcp_result,
    generate_mcp,
    make_parser,
    regenerate_2k_mcp,
    resume_mcp,
)


def h3_singularity_inventory(*, regen_readiness: str = "ready", interp_readiness: str = "ready") -> dict:
    return {
        "tools": ["video.create", "video.preflight", "video.regenerate_2k", "video.status", "video.result"],
        "schemas": schemas(),
        "create_tool": "video.create",
        "create_supports_title": True,
        "capabilities": {
            "default_route": "h3-singularity",
            "default_model": "minimax-h3-ref2va",
            "routes": [
                {
                    "route": "h3-singularity",
                    "model": "minimax-h3-ref2va",
                    "configured": True,
                    "quality_profiles": ["fasth3-nvfp4", "du-0"],
                    "duration_seconds": {"minimum": 4, "maximum": 15},
                    "aspect_ratios": ["21:9", "16:9", "4:3", "1:1", "3:4", "9:16", "adaptive"],
                }
            ],
            "postprocess": {
                "regenerate_2k": {
                    "profile": "open_h3_2k_ic",
                    "configured": True,
                    "readiness": regen_readiness,
                    "grid_modes": ["budget_1440p", "exact_2x"],
                },
                "interpolate": {
                    "profile": "rife_2x",
                    "configured": True,
                    "readiness": interp_readiness,
                },
            },
        },
    }


class Regenerate2kClientTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_video_client_fal.VideoClientFalTests()

    def test_grid_mode_without_regenerate_2k_is_rejected_before_network(self):
        inventory = h3_singularity_inventory()
        with tempfile.TemporaryDirectory() as root, mock.patch(
            "video_client.check_mcp_tools", return_value=inventory
        ), mock.patch("video_client.call_mcp_tool") as call:
            args = self.fixture.args(
                route="h3-singularity",
                ratio="16:9",
                regenerate_2k=False,
                grid_mode="exact_2x",
            )
            with self.assertRaisesRegex(ClientError, "--grid-mode requires --regenerate-2k"):
                generate_mcp(self.fixture.config(root), args)
            call.assert_not_called()

    def test_unready_regenerate_2k_capability_blocks_generate_and_standalone_before_network(self):
        inventory = h3_singularity_inventory(regen_readiness="not_ready")
        with tempfile.TemporaryDirectory() as root, mock.patch(
            "video_client.check_mcp_tools", return_value=inventory
        ), mock.patch("video_client.call_mcp_tool") as call:
            gen_args = self.fixture.args(
                route="h3-singularity",
                ratio="16:9",
                regenerate_2k=True,
                grid_mode="budget_1440p",
            )
            with self.assertRaisesRegex(ClientError, "open_h3_2k_ic"):
                generate_mcp(self.fixture.config(root), gen_args)
            regen_args = make_parser().parse_args(
                [
                    "regenerate-2k",
                    "--project-id",
                    "demo",
                    "--idempotency-key",
                    "regen-001",
                    "--source-artifact-id",
                    "art_" + "1" * 32,
                ]
            )
            with self.assertRaisesRegex(ClientError, "open_h3_2k_ic"):
                regenerate_2k_mcp(self.fixture.config(root), regen_args)
            call.assert_not_called()

    def test_interpolate_only_remains_byte_identical_and_full_chain_forwards_quality_and_grid(self):
        inventory = h3_singularity_inventory()
        with tempfile.TemporaryDirectory() as root, mock.patch(
            "video_client.check_mcp_tools", return_value=inventory
        ), mock.patch(
            "video_client.call_mcp_tool",
            return_value={"video_task_id": "video_task_" + "a" * 32, "status": "queued"},
        ) as call:
            legacy_args = self.fixture.args(
                route="h3-singularity",
                ratio="16:9",
                idempotency_key="legacy-interp-v1",
                interpolate=True,
            )
            generate_mcp(self.fixture.config(root), legacy_args)
            self.assertEqual(
                json.dumps(call.call_args_list[1].args[2]["postprocess"], sort_keys=True, separators=(",", ":")),
                '{"interpolate":{"profile":"rife_2x"}}',
            )

            call.reset_mock()
            chain_args = self.fixture.args(
                route="h3-singularity",
                ratio="16:9",
                idempotency_key="chain-2k-48fps-v1",
                quality_profile="fasth3-nvfp4",
                regenerate_2k=True,
                grid_mode="exact_2x",
                interpolate=True,
            )
            generate_mcp(self.fixture.config(root), chain_args)
            self.assertEqual([c.args[1] for c in call.call_args_list], ["video.preflight", "video.create"])
            for entry in call.call_args_list:
                payload = entry.args[2]
                self.assertEqual(payload["quality_profile"], "fasth3-nvfp4")
                self.assertEqual(
                    payload["postprocess"],
                    {
                        "regenerate_2k": {"profile": "open_h3_2k_ic", "grid_mode": "exact_2x"},
                        "interpolate": {"profile": "rife_2x"},
                    },
                )

    def test_standalone_regenerate_2k_request_snapshot_preflight_and_idempotency_conflict(self):
        inventory = h3_singularity_inventory()
        src_id = "art_" + "2" * 32
        task_id = "video_task_" + "b" * 32
        with tempfile.TemporaryDirectory() as root, mock.patch(
            "video_client.check_mcp_tools", return_value=inventory
        ), mock.patch(
            "video_client.call_mcp_tool",
            side_effect=[
                {"status": "validated", "can_submit": True},
                {"video_task_id": task_id, "status": "queued"},
            ],
        ) as call:
            parsed = make_parser().parse_args(
                [
                    "regenerate-2k",
                    "--project-id",
                    "demo",
                    "--idempotency-key",
                    "regen-standalone-v1",
                    "--source-artifact-id",
                    src_id,
                    "--grid-mode",
                    "budget_1440p",
                    "--prompt",
                    "cinematic rain reflection",
                    "--seed",
                    "42",
                    "--title",
                    "雨夜街景 2K 重生成",
                    "--interpolate",
                ]
            )
            out = regenerate_2k_mcp(self.fixture.config(root), parsed)
            self.assertEqual(out["video_task_id"], task_id)
            self.assertEqual([c.args[1] for c in call.call_args_list], ["video.preflight", "video.regenerate_2k"])
            self.assertEqual(
                call.call_args_list[1].args[2],
                {
                    "project_id": "demo",
                    "idempotency_key": "regen-standalone-v1",
                    "source_artifact_id": src_id,
                    "profile": "open_h3_2k_ic",
                    "grid_mode": "budget_1440p",
                    "prompt": "cinematic rain reflection",
                    "seed": 42,
                    "postprocess": {"interpolate": {"profile": "rife_2x"}},
                    "title": "雨夜街景 2K 重生成",
                },
            )
            replay = regenerate_2k_mcp(self.fixture.config(root), parsed)
            self.assertEqual(replay["video_task_id"], task_id)
            self.assertEqual(call.call_count, 2)

            conflicting = make_parser().parse_args(
                [
                    "regenerate-2k",
                    "--project-id",
                    "demo",
                    "--idempotency-key",
                    "regen-standalone-v1",
                    "--source-artifact-id",
                    src_id,
                    "--grid-mode",
                    "exact_2x",
                ]
            )
            with self.assertRaisesRegex(ClientError, "different inputs"):
                regenerate_2k_mcp(self.fixture.config(root), conflicting)

    def test_p3_failure_preserves_m1_and_m2_with_interpolate_retry_guidance_and_final_download(self):
        task_id = "video_task_" + "c" * 32
        m1_id = "art_" + "3" * 32
        m2_id = "art_" + "4" * 32
        m3_id = "art_" + "5" * 32
        failed_result = {
            "video_task_id": task_id,
            "project_id": "demo",
            "status": "succeeded",
            "artifact_id": m1_id,
            "delivery": {
                "status": "failed",
                "stage": "interpolation",
                "source_artifact_id": m1_id,
                "regenerate_2k_artifact_id": m2_id,
                "final_artifact_id": None,
            },
        }
        with mock.patch("video_client.mcp_status", return_value=failed_result), mock.patch(
            "video_client.mcp_result", return_value=failed_result
        ), mock.patch("video_client.generate_mcp") as gen_mock:
            resumed = resume_mcp(None, task_id, 1, 10)
            gen_mock.assert_not_called()
        guidance = resumed["recovery_guidance"]
        self.assertEqual(guidance["failed_stage"], "interpolation")
        self.assertEqual(guidance["recommended_tool"], "video.interpolate")
        self.assertEqual(guidance["recommended_source_artifact_id"], m2_id)
        self.assertTrue(guidance["do_not_rerun_generation"])
        self.assertIn(m2_id, guidance["message"])

        with tempfile.TemporaryDirectory() as root:
            with self.assertRaisesRegex(ClientError, m2_id):
                download_mcp_result(self.fixture.config(root), failed_result, str(Path(root) / "failed.mp4"))

            mp4_bytes = b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom"
            m3_sha = hashlib.sha256(mp4_bytes).hexdigest()
            succeeded_result = {
                "video_task_id": task_id,
                "project_id": "demo",
                "status": "succeeded",
                "artifact_id": m1_id,
                "media": {"sha256": "0" * 64, "size": 16},
                "download_path": f"/artifacts/{m1_id}/content",
                "delivery": {
                    "status": "succeeded",
                    "stage": "completed",
                    "source_artifact_id": m1_id,
                    "regenerate_2k_artifact_id": m2_id,
                    "final_artifact_id": m3_id,
                    "result": {
                        "status": "succeeded",
                        "artifact_id": m3_id,
                        "media": {"sha256": m3_sha, "size": len(mp4_bytes)},
                        "download_path": f"/artifacts/{m3_id}/content",
                    },
                },
            }

            def fake_native_download(_cfg, doc, dest: Path):
                self.assertEqual(doc["artifact_id"], m3_id)
                dest.write_bytes(mp4_bytes)

            target_mp4 = Path(root) / "m3_final.mp4"
            with mock.patch("video_client._download_native_result", side_effect=fake_native_download), mock.patch(
                "video_client._validate_downloaded_video"
            ):
                downloaded = download_mcp_result(self.fixture.config(root), succeeded_result, str(target_mp4))
            self.assertEqual(downloaded["download"]["artifact_id"], m3_id)
            self.assertEqual(target_mp4.read_bytes(), mp4_bytes)


if __name__ == "__main__":
    unittest.main()
