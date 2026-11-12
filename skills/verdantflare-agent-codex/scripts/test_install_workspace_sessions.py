import contextlib
import io
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch, MagicMock

import install
import workspace_sessions


class InstallerOptionTests(unittest.TestCase):
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

    def test_default_install_never_constructs_patch(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(workspace_sessions,"Patch") as factory:
            result,output,process,sync,register=self.run_installer(directory,[])
            self.assertEqual(result,0);factory.assert_not_called();register.assert_not_called()
            self.assertIn('"workspace_sessions": "not-requested"',output)

    def test_explicit_option_probes_and_applies_once(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(workspace_sessions,"Patch") as factory:
            factory.return_value.probe.return_value="compatible"
            factory.return_value.apply.return_value="installed-reload-required"
            result,output,process,sync,register=self.run_installer(directory,["--workspace-sessions"])
            self.assertEqual(result,0);factory.return_value.probe.assert_called_once()
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

    def test_apply_failure_is_not_reported_as_success(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(workspace_sessions,"Patch") as factory:
            factory.return_value.probe.return_value="compatible"
            factory.return_value.apply.side_effect=workspace_sessions.PatchError("drift")
            with self.assertRaises(workspace_sessions.PatchError):self.run_installer(directory,["--workspace-sessions"])


if __name__=="__main__":unittest.main()
