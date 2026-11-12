import json
import os
import re
import shutil
import subprocess
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import workspace_sessions as ws


class HeaderBindingTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "Node is required for binding execution")
    def test_notice_creation_survives_component_local_react_name_shadow(self):
        # The bundled $n component has a local k, shadowing the module's React
        # loader. Execute the actual injected expression in that lexical shape.
        original = b"\n".join([
            b"wi as z,", b"IN as lt,", b"function kn(e,t,n){let r=Xe()",
            b"clientThreadIdsByConversationId:c}),u.current)",
            b"[e,n,c,l,t,r,i])", b"o=i===void 0||i,c=te()",
            b"children:[M,N,F]",
        ])
        rendered = ws.render_header(original).decode("utf-8")
        children = re.search(r"children:(\[[^\n]+\])", rendered).group(1)
        bindings = re.sub(r"^import .*?;\n", "", (ws.RESOURCES / "bindings.js.txt").read_text("utf-8"), flags=re.M)
        program = """
const assert = require('node:assert/strict');
function k(){return {createElement:type=>({type})}}
""" + bindings + """
function homepage(){
  let k = false, M = 'before', N = 'list', F = 'after';
  return """ + children + """;
}
const children = homepage();
assert.equal(children[1].type, vfNotice);
assert.deepEqual([children[0], ...children.slice(2)], ['before','list','after']);
"""
        result = subprocess.run([shutil.which("node"), "-e", program], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


class PatchLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.extension = self.root / "extension"
        self.extension.mkdir()
        (self.extension / "package.json").write_text(json.dumps({"publisher":"openai","name":"chatgpt","version":"test"}))
        self.original = b"original-header"
        (self.extension / "webview/assets").mkdir(parents=True)
        self.target = self.extension / "webview/assets/header.js"
        self.target.write_bytes(self.original)
        self.manager = ws.Patch(self.extension, self.root / "state")
        self.manager.adapter = {"version":"test","target":"webview/assets/header.js","target_sha256":ws.sha(self.original),"dependencies":{}}
        self.manager.target = self.target
        self.render = patch.object(ws, "render_header", lambda original: original+b"-patched")
        self.render.start();self.addCleanup(self.render.stop)

    def test_probe_does_not_write_and_apply_is_idempotent(self):
        self.assertEqual(self.manager.probe(), "compatible")
        self.assertFalse(self.manager.state.exists())
        self.assertEqual(self.manager.apply(), "installed-reload-required")
        self.assertEqual(self.manager.apply(), "already-installed")
        self.assertEqual(self.manager.probe(), "installed")
        self.assertEqual(self.manager.rollback(), "restored-reload-required")
        self.assertEqual(self.target.read_bytes(), self.original)
        self.assertFalse(self.manager.sidecar.exists())
        self.manager.apply()
        self.assertEqual(self.manager.probe(), "installed")

    def test_foreign_edits_block_apply_or_rollback(self):
        self.target.write_bytes(b"user-edit")
        with self.assertRaises(ws.PatchError):self.manager.apply()
        self.assertEqual(self.target.read_bytes(), b"user-edit")
        self.target.write_bytes(self.original);self.manager.apply()
        self.target.write_bytes(b"later-edit")
        with self.assertRaises(ws.PatchError):self.manager.rollback()
        self.assertEqual(self.target.read_bytes(),b"later-edit")

    def test_corrupt_backup_is_not_restored(self):
        self.manager.apply();before=self.target.read_bytes()
        self.manager.backup.write_bytes(b"bad")
        with self.assertRaises(ws.PatchError):self.manager.rollback()
        self.assertEqual(self.target.read_bytes(),before)

    def test_upgrade_and_dependency_mismatch_refuse(self):
        self.manager.apply()
        pkg=self.extension/"package.json"
        data=json.loads(pkg.read_text());data["version"]="new";pkg.write_text(json.dumps(data))
        before=self.target.read_bytes()
        with self.assertRaises(ws.PatchError):self.manager.rollback()
        self.assertEqual(self.target.read_bytes(),before)
        data["version"]="test";pkg.write_text(json.dumps(data))
        self.manager.adapter["dependencies"]={"missing.js":"bad"}
        with self.assertRaises(ws.PatchError):self.manager.probe()

    def test_failure_after_sidecar_write_recovers_original(self):
        real=ws.atomic_write
        def fail_target(path,data):
            if path==self.target and data!=self.original:raise OSError("simulated disk failure")
            return real(path,data)
        with patch.object(ws,"atomic_write",fail_target):
            with self.assertRaises(OSError):self.manager.apply()
        self.assertEqual(self.target.read_bytes(),self.original)
        self.assertFalse(self.manager.sidecar.exists())
        self.assertEqual(self.manager.probe(),"compatible")

    def test_interrupted_known_partial_patch_can_rollback(self):
        self.manager.apply();self.target.write_bytes(self.original)
        self.assertEqual(self.manager.probe(),"interrupted")
        self.manager.rollback();self.assertFalse(self.manager.sidecar.exists())

    def test_older_patch_requires_explicit_rollback(self):
        self.manager.apply()
        with patch.object(ws,"render_header",lambda original:original+b"-new"):
            self.assertEqual(self.manager.probe(),"installed-older-patch")
            with self.assertRaises(ws.PatchError):self.manager.apply()
            self.manager.rollback()
            self.manager.apply()
            self.assertEqual(self.manager.probe(),"installed")

    def test_lock_prevents_second_writer(self):
        with self.manager.lock():
            with self.assertRaises(ws.PatchError):self.manager.apply()
        self.assertEqual(self.target.read_bytes(),self.original)


if __name__ == "__main__":unittest.main()
