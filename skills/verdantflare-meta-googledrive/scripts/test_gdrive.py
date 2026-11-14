import argparse
import contextlib
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
import unittest.mock

import gdrive
import install


class PreviewInstallerTests(unittest.TestCase):
    def test_reproducible_archive_and_manifest(self):
        import zipfile
        import xml.etree.ElementTree as ET
        with tempfile.TemporaryDirectory() as directory:
            first = install.build_vsix(Path(directory) / "first.vsix")
            second = install.build_vsix(Path(directory) / "second.vsix")
            self.assertEqual(first.read_bytes(), second.read_bytes())
            with zipfile.ZipFile(first) as archive:
                self.assertEqual(set(archive.namelist()),
                                 {"extension/" + n for n in install.EXTENSION_FILES} |
                                 {"extension.vsixmanifest", "[Content_Types].xml"})
                for name in ("extension.vsixmanifest", "[Content_Types].xml"):
                    ET.fromstring(archive.read(name))
                self.assertNotIn("__metadata", json.loads(archive.read("extension/package.json")))

    def test_check_is_read_only_and_failure_propagates(self):
        with unittest.mock.patch.object(install, "find_code", return_value="code"), \
             unittest.mock.patch.object(install, "install") as copy, \
             unittest.mock.patch.object(install, "run_code", return_value="verdantflare.gdrive-markdown-preview@0.2.0\n"):
            install.main(["--check-vscode"])
            copy.assert_not_called()
        with unittest.mock.patch.object(install, "run_code", return_value="wrong@0.1.0"):
            with self.assertRaises(ValueError):
                install.check_vscode("code")
        with unittest.mock.patch.object(install, "run_code", side_effect=subprocess.CalledProcessError(1, "code")):
            with self.assertRaises(subprocess.CalledProcessError):
                install.install_vscode("code")

    def test_modes_and_missing_explicit_cli(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            install.main(["--check-vscode", "--vscode"])
        with self.assertRaises(ValueError):
            install.find_code("nonexistent-gdrive-editor-cli")

    @unittest.skipUnless(install.os.name == "nt", "Windows CLI layout")
    def test_versioned_windows_cli_without_shell(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            wrapper = root / "bin" / "code.cmd"
            wrapper.parent.mkdir()
            cli = root / "123abc/resources/app/out/cli.js"
            cli.parent.mkdir(parents=True)
            cli.write_text("", encoding="utf-8")
            (root / "Code.exe").write_bytes(b"")
            wrapper.write_text('"%~dp0..\\Code.exe" "%~dp0..\\123abc\\resources\\app\\out\\cli.js" %*', encoding="utf-8")
            with unittest.mock.patch.object(install.subprocess, "run") as run:
                run.return_value.stdout = "ok"
                self.assertEqual(install.run_code(str(wrapper), ["--list-extensions"]), "ok")
                self.assertEqual(run.call_args.args[0], [str(root / "Code.exe"), str(cli), "--list-extensions"])
                self.assertEqual(run.call_args.kwargs["env"]["ELECTRON_RUN_AS_NODE"], "1")
                self.assertNotIn("shell", run.call_args.kwargs)


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.project = self.base / "project"
        self.drive = self.base / "drive"
        self.project.mkdir()
        self.drive.mkdir()
        self.call("init", drive_root=str(self.drive), namespace="github.com/team/demo")

    def call(self, command, **kwargs):
        args = dict(project=str(self.project), command=command, key=None, apply=False, doc=None,
                    adopt_local=False, keep_tmp=False)
        args.update(kwargs)
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = gdrive.run(argparse.Namespace(**args))
        return result, json.loads(output.getvalue())

    @property
    def remote(self):
        return self.drive / "github.com/team/demo"

    def put(self, content=b"first", bucket="pending", **extra):
        source = self.project / ".tmp" / "图 example.png"
        source.parent.mkdir(exist_ok=True)
        source.write_bytes(content)
        options = dict(source=str(source), bucket=bucket, name="shots/图 example.png",
                       reviewer=None, evidence=None, reason="test")
        options.update(extra)
        return self.call("put", **options)[1]

    def test_gdrive_is_link_and_put_writes_to_drive(self):
        self.assertEqual((self.project / ".gdrive").resolve(), self.remote.resolve())
        config = json.loads((self.remote / "managed/config.json").read_text(encoding="utf-8"))
        self.assertEqual(config, {"schema": 3, "namespace": "github.com/team/demo"})
        self.assertEqual((self.project / ".gitignore").read_bytes(), b"/.gdrive\n/.tmp/\n")
        self.assertNotIn(b"\r\n", (self.remote / "managed/config.json").read_bytes())
        crlf = self.base / "crlf"
        crlf.mkdir()
        (crlf / ".gitignore").write_bytes(b"/.gdrive/\r\n/.tmp/")
        self.call("init", project=str(crlf), drive_root=str(self.drive), namespace="github.com/team/demo")
        self.assertEqual((crlf / ".gitignore").read_bytes(), b"/.gdrive/\r\n/.tmp/\r\n/.gdrive\r\n")
        key = self.put()["key"]
        self.assertEqual((self.remote / "managed" / key).read_bytes(), b"first")
        self.assertTrue((self.remote / "managed/receipts" / (key + ".json")).is_file())
        self.assertFalse((self.remote / "managed/history").exists())
        other = self.base / "other"
        other.mkdir()
        self.call("init", project=str(other), drive_root=str(self.drive), namespace="github.com/team/demo")
        self.assertEqual((other / ".gdrive/managed" / key).read_bytes(), b"first")
        _, again = self.call("init", drive_root=str(self.drive), namespace="github.com/team/demo")
        self.assertEqual(again["link"], "existing")
        with self.assertRaisesRegex(ValueError, "elsewhere"):
            self.call("init", drive_root=str(self.drive), namespace="github.com/team/other")

    def test_links_and_remove_refusal(self):
        key = self.put()["key"]
        docs = self.project / "docs/archive"
        docs.mkdir(parents=True)
        encoded = ("../../.gdrive/managed/" + key).replace(" ", "%20")
        (docs / "review.md").write_text(
            f'![inline]({encoded})\n\n![reference][x]\n\n[x]: <{encoded}>\n\n'
            f'<video src="{encoded}" poster="{encoded}"></video>\n\n'
            '```md\n![example](missing.png)\n```\n', encoding="utf-8")
        code, audit = self.call("audit")
        self.assertEqual(code, 0)
        self.assertEqual(len(audit["references"]), 4)
        self.assertTrue(all(r["managed"] for r in audit["references"]))
        with self.assertRaisesRegex(ValueError, "Still referenced"):
            self.call("remove", key=key, apply=True)
        (docs / "review.md").write_text('![lost](missing.png)', encoding="utf-8")
        code, audit = self.call("audit")
        self.assertEqual((code, audit["missing"]), (1, 1))
        self.call("remove", key=key)
        self.assertTrue((self.remote / "managed" / key).exists())
        self.call("remove", key=key, apply=True, keep_tmp=True)
        self.assertFalse((self.remote / "managed" / key).exists())
        self.assertFalse((self.remote / "managed/receipts" / (key + ".json")).exists())
        self.assertEqual((self.project / ".tmp/gdrive-removed" / key).read_bytes(), b"first")

    def test_versions_and_approval(self):
        one = self.put()["key"]
        two = self.put(b"second")["key"]
        self.assertNotEqual(one, two)
        self.assertTrue((self.project / ".gdrive/managed" / one).exists())
        with self.assertRaisesRegex(ValueError, "reviewer"):
            self.put(bucket="approved")
        approved = self.put(bucket="approved", reviewer="test-reviewer", evidence="test fixture")
        self.assertTrue(approved["key"].startswith("approved/"))

    def test_verify_flags_modified_and_unversioned_files(self):
        key = self.put()["key"]
        code, report = self.call("verify")
        self.assertEqual((code, report["verified"]), (0, [key]))
        (self.remote / "managed/pending/shots/copy (1).png").write_bytes(b"drive conflict copy")
        code, report = self.call("verify")
        self.assertEqual((code, report["needs_review"]), (1, ["pending/shots/copy (1).png"]))
        (self.remote / "managed" / key).write_bytes(b"edited in place")
        code, report = self.call("verify", key=[key])
        self.assertEqual((code, report["invalid"][0]["key"]), (1, key))
        with self.assertRaisesRegex(ValueError, "modified"):
            self.call("remove", key=key, apply=True)

    def test_human_directories_are_not_adopted_or_changed(self):
        cloud = self.remote / "品牌素材/logo.png"
        cloud.parent.mkdir(parents=True)
        cloud.write_bytes(b"human cloud original")
        (self.project / "review.md").write_text('![logo](.gdrive/品牌素材/logo.png)', encoding="utf-8")
        code, audit = self.call("audit")
        self.assertEqual((code, audit["human_owned"]), (0, 1))
        self.assertFalse(audit["references"][0]["managed"])
        key = self.put()["key"]
        self.call("remove", key=key, apply=True)
        self.assertEqual(cloud.read_bytes(), b"human cloud original")
        with self.assertRaises(ValueError):
            self.call("remove", key="../品牌素材/logo.png", apply=True)
        with self.assertRaises(ValueError):
            self.call("verify", key=["../品牌素材/logo.png"])

    def legacy_project(self, name, content=b"legacy"):
        other = self.base / name
        managed = other / ".gdrive/managed"
        key = f"pending/shots/a--{hashlib.sha256(content).hexdigest()}.png"
        (managed / key).parent.mkdir(parents=True)
        (managed / key).write_bytes(content)
        receipt = managed / "receipts" / (key + ".json")
        receipt.parent.mkdir(parents=True)
        receipt.write_text("{}", encoding="utf-8")
        (managed / "history/pending").mkdir(parents=True)
        (managed / "history/pending/old.png").write_bytes(b"old")
        (managed / "config.json").write_text('{"schema": 2}', encoding="utf-8")
        return other, key

    def test_adopt_local_moves_assets_and_keeps_history_out_of_drive(self):
        other, key = self.legacy_project("legacy")
        options = dict(project=str(other), drive_root=str(self.drive), namespace="github.com/team/legacy")
        with self.assertRaisesRegex(ValueError, "adopt-local"):
            self.call("init", **options)
        _, report = self.call("init", adopt_local=True, **options)
        remote = self.drive / "github.com/team/legacy/managed"
        self.assertEqual((remote / key).read_bytes(), b"legacy")
        self.assertTrue((remote / "receipts" / (key + ".json")).is_file())
        self.assertFalse((remote / "history").exists())
        self.assertEqual(json.loads((remote / "config.json").read_text(encoding="utf-8"))["schema"], 3)
        self.assertIn("history/pending/old.png", report["kept_out_of_drive"])
        self.assertTrue((Path(report["local_backup"]) / "managed/history/pending/old.png").is_file())
        self.assertEqual((other / ".gdrive").resolve(), (self.drive / "github.com/team/legacy").resolve())

    def test_adopt_local_refuses_conflicts_and_human_dirs(self):
        other, key = self.legacy_project("conflict")
        remote = self.drive / "github.com/team/conflict/managed" / key
        remote.parent.mkdir(parents=True)
        remote.write_bytes(b"different")
        with self.assertRaisesRegex(ValueError, "conflict"):
            self.call("init", project=str(other), drive_root=str(self.drive),
                      namespace="github.com/team/conflict", adopt_local=True)
        self.assertTrue((other / ".gdrive/managed" / key).is_file())
        self.assertIsNone(gdrive.link_target(other))
        human, _ = self.legacy_project("human")
        (human / ".gdrive/manual").mkdir()
        with self.assertRaisesRegex(ValueError, "Human directories"):
            self.call("init", project=str(human), drive_root=str(self.drive),
                      namespace="github.com/team/human", adopt_local=True)

    def test_path_and_mount_protection(self):
        for key in ("../escape", "C:/escape", "a/.gdrive/b", "a/../../b", "a/NUL", "/absolute"):
            with self.subTest(key=key), self.assertRaises(ValueError):
                gdrive.relative(key)
        lone = self.base / "lone"
        lone.mkdir()
        with self.assertRaisesRegex(ValueError, "unavailable"):
            self.call("init", project=str(lone), drive_root=str(self.base / "unmounted"),
                      namespace="github.com/team/x")
        self.assertFalse((self.base / "unmounted").exists())
        self.drive.rename(self.base / "drive-offline")
        with self.assertRaisesRegex(ValueError, "unavailable"):
            self.call("status")
        self.assertFalse(self.drive.exists())

    def test_git_tracked_exempt_and_doc_scope(self):
        subprocess.run(["git", "init", "-q", str(self.project)], check=True)
        tracked = self.project / "images/a.png"
        tracked.parent.mkdir()
        tracked.write_bytes(b"tracked")
        (self.project / ".tmp/b.png").parent.mkdir(exist_ok=True)
        (self.project / ".tmp/b.png").write_bytes(b"temp")
        subprocess.run(["git", "-C", str(self.project), "add", "images/a.png"], check=True)
        (self.project / "good.md").write_text("![a](images/a.png)", encoding="utf-8")
        (self.project / "bad.md").write_text("![b](.tmp/b.png)", encoding="utf-8")
        code, audit = self.call("audit", doc=["good.md"])
        self.assertEqual((code, audit["git_exempt"], audit["unmanaged"]), (0, 1, 0))
        self.assertEqual(audit["references"][0]["owner"], "git")
        code, audit = self.call("audit")
        self.assertEqual((code, audit["unmanaged"]), (1, 1))
        with self.assertRaisesRegex(ValueError, "escapes"):
            self.call("audit", doc=["../outside.md"])
        status = subprocess.run(["git", "-C", str(self.project), "status", "--porcelain", "--ignored"],
                                capture_output=True, text=True, check=True).stdout
        self.assertNotIn("?? .gdrive", status)

    @unittest.skipUnless(gdrive.os.name == "nt", "junction fallback is Windows-only")
    def test_junction_fallback_without_symlink_rights(self):
        target = self.drive / "github.com/team/junction"
        target.mkdir(parents=True)
        link = self.base / "junction-project/.gdrive"
        link.parent.mkdir()
        with unittest.mock.patch.object(gdrive.os, "symlink", side_effect=OSError("no privilege")):
            self.assertEqual(gdrive.make_link(link, target.resolve()), "junction")
        self.assertEqual(gdrive.link_target(link.parent), target.resolve())

    def test_receipts_accumulate(self):
        self.put(reason="first")
        record = self.put(reason="second")
        self.assertEqual([x["reason"] for x in record["imports"]], ["first", "second"])

    def test_install_update_preserves_backup(self):
        root = self.base / "global/skills"
        with contextlib.redirect_stdout(io.StringIO()):
            target = install.install(root)
            self.assertEqual(install.fingerprint(target), install.fingerprint(Path(install.__file__).resolve().parents[1]))
            install.install(root)
            self.assertFalse((root.parent / "skill-backups").exists())
            (target / "local-edit.txt").write_text("preserve", encoding="utf-8")
            install.install(root)
        backups = list((root.parent / "skill-backups").iterdir())
        self.assertEqual(len(backups), 1)
        self.assertEqual((backups[0] / "local-edit.txt").read_text(), "preserve")


if __name__ == "__main__":
    unittest.main()
