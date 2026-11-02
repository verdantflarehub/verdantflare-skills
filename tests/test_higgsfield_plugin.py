"""Member installations must work without a developer checkout."""
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

BRIDGE = Path(__file__).resolve().parents[1] / "plugins/verdantflare-higgsfield/scripts/video.py"
spec = importlib.util.spec_from_file_location("higgsfield_plugin", BRIDGE)
plugin = importlib.util.module_from_spec(spec)
spec.loader.exec_module(plugin)


class InstalledPluginTest(unittest.TestCase):
    def test_cached_plugin_resolves_standalone_skill(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            client = root / "client/skills/verdantflare-video/scripts/video_client.py"
            client.parent.mkdir(parents=True)
            client.write_text('STUDIO_VIDEO_CLIENT_CONTRACT = "channel-capabilities-v1"', encoding="utf-8")
            client.parent.parent.joinpath("SKILL.md").write_text("installed skill", encoding="utf-8")
            cached = root / "cache/plugin/scripts/video.py"
            with patch.dict(os.environ, {"CODEX_HOME": str(root / "client")}, clear=True), \
                 patch.object(plugin, "__file__", str(cached)), \
                 patch.object(plugin.Path, "home", return_value=root / "user"), \
                 patch.object(plugin.Path, "cwd", return_value=root):
                self.assertEqual(plugin.resolve(), client)

    def test_draft_args_keep_mixed_references_and_fix_channel(self):
        args = ["generate", "--model", "seedance_2_5_draft", "--resolution", "480p",
                "--image-ref", "image=identity", "--video-ref", "video=motion"]
        self.assertEqual(plugin.arguments(args), args + ["--route", "higgsfield"])
        with self.assertRaises(ValueError):
            plugin.arguments(args + ["--route=other"])

    def test_public_api_edition_is_rejected_without_execution(self):
        with tempfile.TemporaryDirectory() as directory:
            client = Path(directory) / "skills/verdantflare-video/scripts/video_client.py"
            client.parent.mkdir(parents=True)
            client.write_text("raise RuntimeError('must not execute')", encoding="utf-8")
            client.parent.parent.joinpath("SKILL.md").write_text("public API skill", encoding="utf-8")
            with patch.dict(os.environ, {"VERDANTFLARE_SKILLS_ROOT": directory}), self.assertRaisesRegex(ValueError, "public API edition is incompatible"):
                plugin.resolve()

    def test_explicit_missing_repository_is_not_silently_overridden(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, {"VERDANTFLARE_SKILLS_ROOT": directory}):
                with self.assertRaises(ValueError):
                    plugin.resolve()
