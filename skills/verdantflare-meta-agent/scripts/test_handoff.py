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

    def _claude_handoff(self, stage: str = "claude_arch_audit") -> dict:
        data = copy.deepcopy(self.data)
        data["recipient"].update(service="local-claude", agent="claude", mode="panel")
        data["allowed_write_paths"] = []
        data["deliverables"] = ["docs/reviews/review_20261115_demo_t4.md"]
        data["review_stage"] = stage
        data["review_doc_path"] = (
            "docs/reviews/review_20261115_demo_t4.md"
            if stage == "claude_arch_audit"
            else "docs/reviews/review_20261115_demo_t1.md"
        )
        return data

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

    def test_optional_review_fields_unknown_keys_and_directory_binding(self):
        # Unknown key rejected
        unknown = copy.deepcopy(self.data)
        unknown["extra_key"] = "nope"
        with self.assertRaisesRegex(InvalidHandoff, "incorrect field set"):
            validate(unknown, self.root)

        # Single half of optional pair rejected
        half = copy.deepcopy(self.data)
        half["review_stage"] = "codex_detail"
        with self.assertRaisesRegex(InvalidHandoff, "pair"):
            validate(half, self.root)

        # Valid codex_detail and acceptance_audit directory bindings pass
        codex_rev = copy.deepcopy(self.data)
        codex_rev["review_stage"] = "codex_detail"
        codex_rev["review_doc_path"] = "docs/reviews/review_20261115_demo_t2-r2.md"
        res = validate(codex_rev, self.root)
        self.assertEqual(res["review_stage"], "codex_detail")

        acc_audit = copy.deepcopy(self.data)
        acc_audit["review_stage"] = "acceptance_audit"
        acc_audit["review_doc_path"] = "docs/acceptance/accept_20261115_demo.md"
        self.assertEqual(validate(acc_audit, self.root)["review_stage"], "acceptance_audit")

        # Mismatched directory bindings rejected
        bad_dir1 = copy.deepcopy(codex_rev)
        bad_dir1["review_doc_path"] = "docs/acceptance/accept_20261115_demo.md"
        with self.assertRaisesRegex(InvalidHandoff, "docs/reviews/review_"):
            validate(bad_dir1, self.root)

        bad_dir2 = copy.deepcopy(acc_audit)
        bad_dir2["review_doc_path"] = "docs/reviews/review_20261115_demo_t4.md"
        with self.assertRaisesRegex(InvalidHandoff, "docs/acceptance/accept_"):
            validate(bad_dir2, self.root)

    def test_ho_claude_scope_stage_binding_and_readonly_whitelist(self):
        # Compliant claude_arch_audit, claude_arch, and claude_escalation pass
        for stage in ("claude_arch", "claude_escalation", "claude_arch_audit"):
            ok = self._claude_handoff(stage)
            self.assertEqual(validate(ok, self.root)["review_stage"], stage)

        # Dispatching to Claude without review_stage hits HO-CLAUDE-SCOPE
        no_stage = copy.deepcopy(self.data)
        no_stage["recipient"].update(service="local-claude", agent="claude", mode="panel")
        with self.assertRaisesRegex(InvalidHandoff, "HO-CLAUDE-SCOPE"):
            validate(no_stage, self.root)

        # Dispatching codex_detail or acceptance_audit to Claude hits HO-CLAUDE-SCOPE
        wrong_stage = self._claude_handoff("claude_arch")
        wrong_stage["review_stage"] = "codex_detail"
        with self.assertRaisesRegex(InvalidHandoff, "HO-CLAUDE-SCOPE"):
            validate(wrong_stage, self.root)

        # Dispatching claude_arch_audit to non-Claude hits HO-CLAUDE-SCOPE
        wrong_agent = self._claude_handoff("claude_arch_audit")
        wrong_agent["recipient"].update(service="local-antigravity", agent="antigravity", mode="panel")
        with self.assertRaisesRegex(InvalidHandoff, "HO-CLAUDE-SCOPE"):
            validate(wrong_agent, self.root)

        # Giving Claude write access hits HO-CLAUDE-SCOPE
        writable = self._claude_handoff("claude_arch")
        writable["allowed_write_paths"] = ["docs/reviews/review_20261115_demo_t1.md"]
        with self.assertRaisesRegex(InvalidHandoff, "HO-CLAUDE-SCOPE"):
            validate(writable, self.root)

        # Passing a single .py script in inputs, plan_path, or change_path to Claude hits HO-CLAUDE-SCOPE
        py_file = self.root / "scripts/plan_lint.py"
        py_file.parent.mkdir(parents=True, exist_ok=True)
        py_file.write_text("print('hi')\n", "utf-8")
        py_input = self._claude_handoff("claude_arch")
        py_input["inputs"] = ["scripts/plan_lint.py"]
        with self.assertRaisesRegex(InvalidHandoff, "HO-CLAUDE-SCOPE"):
            validate(py_input, self.root)

        py_plan = self._claude_handoff("claude_arch")
        py_plan["plan_path"] = "scripts/plan_lint.py"
        with self.assertRaisesRegex(InvalidHandoff, "HO-CLAUDE-SCOPE"):
            validate(py_plan, self.root)

        py_change = self._claude_handoff("claude_arch")
        py_change["change_path"] = "scripts/plan_lint.py"
        with self.assertRaisesRegex(InvalidHandoff, "HO-CLAUDE-SCOPE"):
            validate(py_change, self.root)

    def test_ho_claude_scope_total_text_chars_caps_and_anti_split_bypass(self):
        # Splitting long text across secondary list fields (constraints + verification + completion_criteria)
        # cannot bypass the 800-char cap on claude_escalation or 1,000-char cap on claude_arch_audit
        esc = self._claude_handoff("claude_escalation")
        esc["constraints"] = ["甲" * 300]
        esc["verification"] = ["乙" * 300]
        esc["completion_criteria"] = ["丙" * 250]
        with self.assertRaisesRegex(InvalidHandoff, r"HO-CLAUDE-SCOPE: total_text_chars .* claude_escalation limit \(800\)"):
            validate(esc, self.root)

        audit = self._claude_handoff("claude_arch_audit")
        audit["constraints"] = ["甲" * 400]
        audit["verification"] = ["乙" * 350]
        audit["completion_criteria"] = ["丙" * 300]
        with self.assertRaisesRegex(InvalidHandoff, r"HO-CLAUDE-SCOPE: total_text_chars .* claude_arch_audit limit \(1000\)"):
            validate(audit, self.root)

        # Stuffing large path arrays (e.g. 40 deliverables) also counts toward total_text_chars and hits HO-CLAUDE-SCOPE
        deliverables_flood = self._claude_handoff("claude_arch_audit")
        deliverables_flood["deliverables"] = [f"docs/reviews/review_20261115_item{i:02d}_t4.md" for i in range(40)]
        with self.assertRaisesRegex(InvalidHandoff, r"HO-CLAUDE-SCOPE: total_text_chars .* claude_arch_audit limit \(1000\)"):
            validate(deliverables_flood, self.root)

        arch = self._claude_handoff("claude_arch")
        arch["goal"] = "丁" * 1201
        with self.assertRaisesRegex(InvalidHandoff, r"HO-CLAUDE-SCOPE: total_text_chars .* claude_arch limit \(1200\)"):
            validate(arch, self.root)

        # Smuggling long or non-ASCII prose in identifier field also hits HO-CLAUDE-SCOPE
        smuggle = self._claude_handoff("claude_arch_audit")
        smuggle["assignment_id"] = "id-" + ("x" * 200)
        with self.assertRaisesRegex(InvalidHandoff, "HO-CLAUDE-SCOPE"):
            validate(smuggle, self.root)

        smuggle_cn = self._claude_handoff("claude_arch_audit")
        smuggle_cn["task_key"] = "塞入中文自然语言指令"
        with self.assertRaisesRegex(InvalidHandoff, "HO-CLAUDE-SCOPE"):
            validate(smuggle_cn, self.root)


if __name__ == "__main__":
    unittest.main()
