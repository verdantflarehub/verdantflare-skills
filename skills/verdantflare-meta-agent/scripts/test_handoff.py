import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from check_handoff import InvalidHandoff, validate

SAMPLE = Path(__file__).resolve().parents[1] / "resources/handoff.example.json"


class HandoffTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.data = json.loads(SAMPLE.read_text("utf-8"))
        for key in ("plan_path", "change_path"):
            p = self.root / self.data[key]
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("# Example\n", "utf-8")
        p = self.root / self.data["inputs"][0]
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("# Contract\n", "utf-8")

    def test_valid_and_revision_preserve_task_identity(self):
        original = validate(self.data, self.root)
        self.data["revision"] = 2
        revised = validate(self.data, self.root)
        self.assertEqual(original["task_key"], revised["task_key"])
        self.assertEqual(revised["revision"], 2)

    def test_missing_required_fields(self):
        for key in self.data:
            with self.subTest(key=key):
                data = copy.deepcopy(self.data)
                del data[key]
                with self.assertRaises(InvalidHandoff):
                    validate(data, self.root)

    def test_invalid_types_and_modes(self):
        for key, value in (("revision", True), ("revision", 0), ("goal", ""), ("verification", []), ("inputs", "x")):
            data = copy.deepcopy(self.data)
            data[key] = value
            with self.subTest(key=key, value=value), self.assertRaises(InvalidHandoff):
                validate(data, self.root)
        self.data["recipient"]["mode"] = "plugin"
        with self.assertRaises(InvalidHandoff):
            validate(self.data, self.root)

    def test_path_escape_and_absolute_rejected(self):
        for value in ("../escape", "C:/secret", "C:secret", "/secret", "a/../../secret", "//server/share", "a\\b", "file:stream"):
            self.data["allowed_write_paths"] = [value]
            with self.subTest(value=value), self.assertRaises(InvalidHandoff):
                validate(self.data, self.root)

    def test_missing_document_and_identity_conflict(self):
        data = copy.deepcopy(self.data)
        data["plan_path"] = "missing.md"
        with self.assertRaises(InvalidHandoff):
            validate(data, self.root)
        self.data["reply_to"]["native_session_id"] = "another"
        with self.assertRaises(InvalidHandoff):
            validate(self.data, self.root)

    def test_cli_duplicate_keys_no_private_path_leak(self):
        input_file = self.root / "private-handoff.json"
        input_file.write_text('{"plan_id":"a","plan_id":"b"}', "utf-8")
        proc = subprocess.run([sys.executable, str(Path(__file__).with_name("check_handoff.py")),
                               "--workspace", str(self.root), "--input", str(input_file)],
                              capture_output=True, text=True)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("duplicate key", proc.stderr)
        self.assertNotIn(str(self.root), proc.stderr)
        self.assertEqual(input_file.read_text("utf-8"), '{"plan_id":"a","plan_id":"b"}')


if __name__ == "__main__":
    unittest.main()
