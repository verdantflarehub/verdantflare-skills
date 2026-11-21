import contextlib
import io
import json
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch, MagicMock

import install
import workspace_sessions


class InstallerOptionTests(unittest.TestCase):
    def test_upgrade_uses_registered_extension_and_preserves_explicit_paths(self):
        from verdantflare_agent_codex.plugin import resolve_codex_binary, resolve_codex_extension
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / ".vscode" / "extensions"
            registered = root / "openai.chatgpt-2"
            old = root / "openai.chatgpt-1"
            for folder in (registered, old):
                folder.mkdir(parents=True)
                (folder / "package.json").write_text("{}")
            entry = {"identifier": {"id": "OpenAI.chatgpt"}, "relativeLocation": registered.name}
            (root / "extensions.json").write_text(json.dumps([entry]))
            with patch.object(Path, "home", return_value=Path(directory)):
                self.assertEqual(install.auto_find_codex_extension(), registered)
            self.assertEqual(resolve_codex_extension(root), registered)
            self.assertEqual(resolve_codex_extension(old), old)
            binary = old / "codex.exe"
            binary.touch()
            self.assertEqual(resolve_codex_binary(binary, root), str(binary))
            (root / ".obsolete").write_text(json.dumps({registered.name: True}))
            with self.assertRaises(FileNotFoundError):
                resolve_codex_extension(root)

    def test_upgrade_preserves_project_window_and_workspace_scope(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            panel = {"client": str(base / "client.exe"), "hosts": [
                {"cwd": str(base), "socket": str(base / "panel.sock")}
            ]}
            settings = base / "panel.json"
            settings.write_text(json.dumps(panel), encoding="utf-8")
            self.run_installer(directory, ["--panel-transport", str(settings), "--allow-root", directory])
            self.run_installer(directory, ["--replace"])
            config = json.loads((base / "runtime" / "config.json").read_text("utf-8"))
            self.assertEqual(config["panel_transport"], panel)
            self.assertEqual(config["allowed_roots"], [str(base.resolve())])

    def run_installer(self, directory, extra):
        base = Path(directory)
        args = ["--extension",str(base/"extension"),"--codex",str(base/"codex.exe"),
                "--node",str(base/"node.exe"),"--target",str(base/"runtime"),"--skill-target",str(base/"skill"),*extra]
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(install.venv,"EnvBuilder"))
            process = stack.enter_context(patch.object(install.subprocess,"run"))
            sync = stack.enter_context(patch.object(install,"sync_skill_tree",return_value={"SKILL.md":"hash"}))
            stack.enter_context(patch.object(install,"sync_service_tree",return_value={"__main__.py":"hash"}))
            register = stack.enter_context(patch.object(install,"register_peer_hosts"))
            output=io.StringIO();stack.enter_context(contextlib.redirect_stdout(output))
            result=install.main(args)
            return result,output.getvalue(),process,sync,register

    def test_default_install_checks_compatibility_without_applying(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(workspace_sessions,"Patch") as factory:
            factory.return_value.probe.side_effect=workspace_sessions.PatchError("not adapted to extension future")
            result,output,process,sync,register=self.run_installer(directory,[])
            self.assertEqual(result,0);factory.return_value.apply.assert_not_called();register.assert_not_called()
            self.assertIn('"workspace_sessions": "not-requested"',output)
            self.assertIn('not adapted to extension future',output)
            self.assertIn('"status": "blocked"',output)

    def test_explicit_option_probes_and_applies_once(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(workspace_sessions,"Patch") as factory:
            factory.return_value.probe.return_value="compatible"
            factory.return_value.apply.return_value="installed-reload-required"
            factory.return_value.adapter={"version":"test"}
            result,output,process,sync,register=self.run_installer(directory,["--workspace-sessions"])
            self.assertEqual(result,0);self.assertEqual(factory.return_value.probe.call_count,2)
            factory.return_value.apply.assert_called_once();register.assert_not_called()
            self.assertIn("installed-reload-required",output)

    def test_incompatible_patch_stops_before_runtime_install(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(workspace_sessions,"Patch") as factory:
            factory.return_value.probe.side_effect=workspace_sessions.PatchError("unsupported")
            with patch.object(install.subprocess,"run") as process:
                with self.assertRaises(workspace_sessions.PatchError):self.run_installer(directory,["--workspace-sessions"])
                process.assert_not_called()
            self.assertFalse((Path(directory)/"runtime").exists())
            factory.return_value.apply.assert_not_called()

    def test_auto_detected_extension_is_stored_version_free(self):
        with tempfile.TemporaryDirectory() as directory:
            base=Path(directory);ext=base/"openai.chatgpt-1.0.0-win32-x64";ext.mkdir()
            args=["--node",str(base/"node.exe"),"--target",str(base/"runtime"),"--skill-target",str(base/"skill")]
            with contextlib.ExitStack() as stack:
                stack.enter_context(patch.object(install,"auto_find_codex_extension",return_value=ext))
                stack.enter_context(patch.object(install,"auto_find_codex_binary",return_value=ext/"codex.exe"))
                stack.enter_context(patch.object(install.venv,"EnvBuilder"))
                stack.enter_context(patch.object(install.subprocess,"run"))
                stack.enter_context(patch.object(install,"sync_skill_tree",return_value={}))
                stack.enter_context(patch.object(install,"sync_service_tree",return_value={}))
                stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
                self.assertEqual(install.main(args),0)
            config=install.json.loads((base/"runtime"/"config.json").read_text("utf-8"))
            root=str(Path.home()/".vscode"/"extensions")
            self.assertEqual(config["extension"],root);self.assertEqual(config["codex"],root)

    def test_apply_failure_is_not_reported_as_success(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(workspace_sessions,"Patch") as factory:
            factory.return_value.probe.return_value="compatible"
            factory.return_value.apply.side_effect=workspace_sessions.PatchError("drift")
            with self.assertRaises(workspace_sessions.PatchError):self.run_installer(directory,["--workspace-sessions"])


if __name__=="__main__":unittest.main()
