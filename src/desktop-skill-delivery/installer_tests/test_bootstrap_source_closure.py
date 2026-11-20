"""Import the actual exported installer sources outside the repository path."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS / 'installer_tools'))
import bootstrap_bundle as bundle


class BootstrapSourceClosureTests(unittest.TestCase):
    def test_projected_file_and_native_entrypoints_import_without_repo(self):
        base = TOOLS.parents[2] / '.tmp/bootstrap-source-closure-tests'
        base.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=base) as folder:
            root = Path(folder)
            payload = bundle.source_resources({'fixture': 'source-projection-only'})
            prefix = 'resources/installer/core/'
            for name, raw in payload.items():
                if name.startswith(prefix):
                    path = root / name.removeprefix(prefix)
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(raw)
            script = ('import sys; from pathlib import Path; root=Path(sys.argv[1]).resolve(); '
                'sys.path.insert(0,str(root)); import offline_operation, native_install_operation, tool_name_diagnostic, host_discovery, host_workbuddy; '
                'from runtime_tools import runtime_lifecycle, native_lifecycle; '
                'mods=[offline_operation,native_install_operation,runtime_lifecycle,native_lifecycle,tool_name_diagnostic,host_discovery,host_workbuddy]; '
                'assert all(Path(m.__file__).resolve().is_relative_to(root) for m in mods); '
                'print("EXPORTED_SOURCE_IMPORTS_VERIFIED")')
            def run():
                return subprocess.run([sys.executable, '-I', '-B', '-c', script, str(root)], cwd=root,
                    env={'PATH': '', 'PYTHONDONTWRITEBYTECODE': '1'}, capture_output=True, timeout=20)
            result = run()
            self.assertEqual(result.returncode, 0, 'Exported installer import closure is incomplete')
            self.assertEqual(result.stdout.strip(), b'EXPORTED_SOURCE_IMPORTS_VERIFIED')
            # The probe must actually require the exported dependency and must
            # not silently recover it from the source repository or cwd.
            (root / 'host_deepseek_journal.py').unlink()
            self.assertNotEqual(run().returncode, 0)


if __name__ == '__main__':
    unittest.main()
