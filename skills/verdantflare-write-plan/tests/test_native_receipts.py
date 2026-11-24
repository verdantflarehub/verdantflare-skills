"""Synthetic provider fixtures test integrity; they never represent real approvals."""
from __future__ import annotations

import copy
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import native_receipts as nr
import plan_lint as pl
from test_plan_lint import build_valid_complex_workspace, run_cli


class Capture:
    """Small provider-shaped evidence, named explicitly as unit test fixtures."""

    def __init__(self, root, provider="antigravity-hub", stage="t2", plan=None, scope=None, tag="unit"):
        self.root = root
        self.folder = root / "evidence" / tag
        self.folder.mkdir(parents=True, exist_ok=True)
        plan = plan or root / "docs/plan/2026.11.unit.plan.md"
        plan.parent.mkdir(parents=True, exist_ok=True)
        if not plan.exists():
            plan.write_text("# Synthetic test plan\n", encoding="utf-8")
        acceptances = list((root / "docs/acceptance").glob("*.md"))
        if acceptances:
            acceptance = acceptances[0]
            design = next((root / "docs/design").glob("*.md"))
        else:
            acceptance = root / "docs/acceptance/accept_20261115_unit.md"
            acceptance.parent.mkdir(parents=True, exist_ok=True)
            acceptance.write_bytes((Path(__file__).parent / "fixtures/locked-exam.md").read_bytes())
            design = root / "docs/design/unit.md"
            design.parent.mkdir(parents=True, exist_ok=True)
            design.write_text("# Unit design\n\n## 1. Current basis\n\nSynthetic scope.\n", encoding="utf-8")
        scope_files = {"acceptance": acceptance.relative_to(root).as_posix()}
        if stage in {"t1", "t2"}:
            scope_files["design"] = design.relative_to(root).as_posix()
        self.input = self.folder / "input.txt"
        self.input.write_text("Fixed reviewed test source\n", encoding="utf-8")
        sid = tag + "-session"
        request, response = "Synthetic review request", "Synthetic review result"
        self.data = {
            "schema_version": 1, "provider": provider,
            "agent": "Gemini" if provider == "antigravity-hub" else "Claude",
            "role": "detail" if provider == "antigravity-hub" else "architecture",
            "stage": stage, "plan_path": plan.relative_to(root).as_posix(),
            "review_hash": scope or pl.compute_review_hash(stage, acceptance, design), "session_id": sid,
            "scope": scope_files,
            "inputs": [self.ref(self.input)], "request_sha256": nr.text_digest(request),
            "response_sha256": nr.text_digest(response), "sources": {}, "native": {},
        }
        if provider == "antigravity-hub":
            self.data["native"] = {"start_step": 10, "end_step": 13}
            self.sources = {
                "transcript": {
                    "source": "hub_rpc", "conversation_id": sid, "start_step": 10, "end_step": 13,
                    "total_steps": 13, "steps": [
                        {"step_index": 10, "type": "CORTEX_STEP_TYPE_USER_INPUT",
                         "status": "CORTEX_STEP_STATUS_DONE", "content": request},
                        {"step_index": 11, "type": "CORTEX_STEP_TYPE_TOOL_CALL",
                         "status": "CORTEX_STEP_STATUS_DONE"},
                        {"step_index": 12, "type": "CORTEX_STEP_TYPE_PLANNER_RESPONSE",
                         "status": "CORTEX_STEP_STATUS_DONE", "content": response},
                    ],
                },
                "observation": {"conversation_id": sid, "status": "CASCADE_RUN_STATUS_IDLE",
                                "num_total_steps": 13, "last_user_input": request, "last_response": response},
            }
        else:
            self.data["native"] = {"request_id": "unit-request", "message_id": "unit-message", "result_id": "unit-result"}
            message = {"id": "unit-message", "role": "assistant", "content": [{"type": "text", "text": response}]}
            self.sources = {
                "stream": [
                    {"type": "assistant", "session_id": sid, "message": copy.deepcopy(message)},
                    {"type": "result", "session_id": sid, "uuid": "unit-result", "subtype": "success",
                     "is_error": False, "terminal_reason": "completed", "result": response},
                ],
                "journal": [
                    {"uuid": "unit-request", "sessionId": sid, "parentUuid": None, "type": "user",
                     "message": {"role": "user", "content": request}},
                    {"uuid": "unit-tool", "sessionId": sid, "parentUuid": "unit-request", "type": "assistant",
                     "message": {"id": "unit-tool-message", "role": "assistant", "content": [{"type": "tool_use", "id": "unit-call"}]}},
                    {"uuid": "unit-tool-result", "sessionId": sid, "parentUuid": "unit-tool", "type": "user",
                     "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "unit-call", "content": "test"}]}},
                    {"uuid": "unit-progress", "sessionId": sid, "parentUuid": "unit-tool-result", "type": "progress"},
                    {"uuid": "unit-answer", "sessionId": sid, "parentUuid": "unit-progress", "type": "assistant",
                     "message": copy.deepcopy(message)},
                ],
                "observation": {"session_id": sid, "exit_code": 0, "completed": True, "stdout_sha256": ""},
            }
        self.path = self.folder / "receipt.json"
        self.save()

    def ref(self, path):
        return {"path": path.relative_to(self.root).as_posix(), "sha256": nr.digest(path.read_bytes())}

    def save(self):
        for key, value in self.sources.items():
            if key == "observation" and "stream" in self.sources:
                value["stdout_sha256"] = self.data["sources"]["stream"]["sha256"]
            path = self.folder / (key + (".jsonl" if isinstance(value, list) else ".json"))
            content = "\n".join(json.dumps(row) for row in value) if isinstance(value, list) else json.dumps(value)
            path.write_text(content, encoding="utf-8")
            self.data["sources"][key] = self.ref(path)
        self.write_descriptor()

    def write_descriptor(self):
        self.path.write_text(json.dumps(self.data), encoding="utf-8")

    def validate(self, **overrides):
        context = {key: self.data[key] for key in ("plan_path", "stage", "role", "review_hash")}
        context.update(overrides)
        return nr.validate_receipt(self.path, self.root, **context)


class NativeReceiptTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_valid_hub_and_claude(self):
        for provider in ("antigravity-hub", "claude-cli"):
            with self.subTest(provider=provider):
                cap = Capture(self.root, provider, tag=provider)
                result = cap.validate()
                self.assertEqual(result.provider, provider)
                self.assertIn(cap.data["session_id"], result.identifiers)
                self.assertEqual(result.input_count, 1)

    def test_malformed_schema_and_scope(self):
        mutations = [
            ("schema_version", True), ("provider", []), ("agent", "Codex"),
            ("session_id", "unavailable"), ("session_id", "n/a"), ("sources", []),
            ("native", {}), ("inputs", []), ("request_sha256", "f" * 64),
        ]
        for field, value in mutations:
            with self.subTest(field=field, value=value):
                cap = Capture(self.root)
                cap.data[field] = value
                cap.write_descriptor()
                with self.assertRaises(nr.ReceiptError):
                    cap.validate()
        cap = Capture(self.root)
        for context in ({"stage": "t4"}, {"role": "architecture"}, {"agent": "Claude"},
                        {"review_hash": "sha256:" + "b" * 64}, {"plan_path": "another-plan.md"}):
            with self.subTest(context=context), self.assertRaises(nr.ReceiptError):
                cap.validate(**context)

    def test_duplicate_json_keys_nonfinite_and_invalid_utf8(self):
        for payload in (b'{"schema_version":1,"schema_version":1}', b'{"x":NaN}', b'\xff', b'[[['):
            with self.subTest(payload=payload):
                cap = Capture(self.root)
                cap.path.write_bytes(payload)
                with self.assertRaises(nr.ReceiptError):
                    cap.validate()

    def test_sources_and_inputs_must_match_live_bytes(self):
        for target in ("input", "transcript"):
            with self.subTest(target=target):
                cap = Capture(self.root)
                path = cap.input if target == "input" else self.root / cap.data["sources"][target]["path"]
                path.write_bytes(path.read_bytes() + b"tampered")
                with self.assertRaises(nr.ReceiptError):
                    cap.validate()
        cap = Capture(self.root)
        duplicate = dict(cap.data["inputs"][0])
        duplicate["path"] = "./" + duplicate["path"]
        cap.data["inputs"].append(duplicate)
        cap.write_descriptor()
        with self.assertRaises(nr.ReceiptError):
            cap.validate()

    def test_relative_paths_and_symlink_escape(self):
        cap = Capture(self.root)
        for path in ("../outside", "/absolute", "C:/file", "evidence\\file", "missing", "\x00"):
            with self.subTest(path=path), self.assertRaises(nr.ReceiptError):
                nr.relative_file(self.root, path)
        with tempfile.TemporaryDirectory() as outside:
            outside = Path(outside)
            (outside / "data").write_bytes(b"unit")
            (self.root / "escape").symlink_to(outside, target_is_directory=True)
            with self.assertRaises(nr.ReceiptError):
                nr.relative_file(self.root, "escape/data")
            (self.root / ".gdrive").mkdir()
            (self.root / ".gdrive/managed").symlink_to(outside, target_is_directory=True)
            self.assertEqual(nr.relative_file(self.root, ".gdrive/managed/data"), (outside / "data").resolve())

    def test_evidence_size_limit_and_streaming_large_input(self):
        cap = Capture(self.root)
        with patch.object(nr, "MAX_SOURCE_BYTES", 8), self.assertRaises(nr.ReceiptError):
            cap.validate()
        cap.input.write_bytes(b"x" * (2 * 1024 * 1024 + 1))
        cap.data["inputs"] = [cap.ref(cap.input)]
        cap.write_descriptor()
        # Successful validation must not use an unbounded Path.read_bytes().
        with patch.object(Path, "read_bytes", side_effect=AssertionError("unbounded read")):
            self.assertEqual(cap.validate().input_count, 1)

    def test_hub_interval_and_terminal_binding(self):
        def bad_step(s): s["transcript"]["steps"][1]["step_index"] = 12
        def missing_step(s): s["transcript"]["steps"].pop(1)
        def second_request(s): s["transcript"]["steps"][1]["type"] = "CORTEX_STEP_TYPE_USER_INPUT"
        def running(s): s["observation"]["status"] = "CASCADE_RUN_STATUS_RUNNING"
        def more_steps(s): s["observation"]["num_total_steps"] = 14
        def another_session(s): s["transcript"]["conversation_id"] = "another"
        def not_hub(s): s["transcript"]["source"] = "file"
        def old_response(s): s["observation"]["last_response"] = "old response"
        def incomplete(s): s["transcript"]["steps"][-1]["status"] = "CORTEX_STEP_STATUS_RUNNING"
        def empty(s): s["transcript"]["steps"][-1]["content"] = ""
        for mutation in (bad_step, missing_step, second_request, running, more_steps, another_session,
                         not_hub, old_response, incomplete, empty):
            with self.subTest(mutation=mutation.__name__):
                cap = Capture(self.root)
                mutation(cap.sources)
                cap.save()
                with self.assertRaises(nr.ReceiptError):
                    cap.validate()

    def test_claude_process_result_and_message_binding(self):
        def failed(s): s["stream"][-1]["is_error"] = True
        def wrong_session(s): s["stream"][-1]["session_id"] = "another"
        def late_event(s): s["stream"].append({"type": "assistant"})
        def duplicate_result(s): s["stream"].append(copy.deepcopy(s["stream"][-1]))
        def nonzero(s): s["observation"]["exit_code"] = 1
        def bool_exit(s): s["observation"]["exit_code"] = False
        def running(s): s["observation"]["completed"] = False
        def wrong_message(s): s["stream"][0]["message"]["id"] = "another"
        def wrong_final(s): s["journal"][-1]["message"]["content"][0]["text"] = "other"
        def wrong_prompt(s): s["journal"][0]["message"]["content"] = "other"
        for mutation in (failed, wrong_session, late_event, duplicate_result, nonzero, bool_exit,
                         running, wrong_message, wrong_final, wrong_prompt):
            with self.subTest(mutation=mutation.__name__):
                cap = Capture(self.root, "claude-cli")
                mutation(cap.sources)
                cap.save()
                with self.assertRaises(nr.ReceiptError):
                    cap.validate()

    def test_claude_ancestry_cannot_skip_requests_or_reuse_ids(self):
        def cycle(s): s["journal"][-1]["parentUuid"] = "unit-answer"
        def missing(s): s["journal"][-1]["parentUuid"] = "absent"
        def second_user(s): s["journal"][2]["message"]["content"] = "Another request"
        def duplicate_id(s): s["journal"].append(copy.deepcopy(s["journal"][0]))
        def mixed_session(s): s["journal"][1]["sessionId"] = "another"
        for mutation in (cycle, missing, second_user, duplicate_id, mixed_session):
            with self.subTest(mutation=mutation.__name__):
                cap = Capture(self.root, "claude-cli")
                mutation(cap.sources)
                cap.save()
                with self.assertRaises(nr.ReceiptError):
                    cap.validate()

    def test_cli_outputs_metadata_without_request_text(self):
        cap = Capture(self.root)
        args = [str(cap.path), "--root", str(self.root), "--plan", cap.data["plan_path"],
                "--stage", "t2", "--role", "detail", "--review-hash", cap.data["review_hash"]]
        output = io.StringIO()
        with patch("sys.stdout", output):
            self.assertEqual(nr.main(args), 0)
        self.assertTrue(json.loads(output.getvalue())["ok"])
        self.assertNotIn("Synthetic review", output.getvalue())
        cap.path.write_bytes(b"invalid")
        with patch("sys.stdout", io.StringIO()):
            self.assertEqual(nr.main(args), 1)

    def test_tool_reminders_allowed_but_new_user_content_rejected(self):
        cap = Capture(self.root, "claude-cli")
        cap.sources["journal"][2]["message"]["content"].append(
            {"type": "text", "text": "<system-reminder>File modified</system-reminder>"})
        cap.save()
        self.assertEqual(cap.validate().agent, "Claude")
        for content in ([{"type": "image", "source": {}}], [],
                        [{"type": "tool_result", "tool_use_id": "unmatched"}],
                        [{"type": "tool_result", "tool_use_id": "unit-call"}, {"type": "text", "text": "New user request"}]):
            with self.subTest(content=content):
                cap.sources["journal"][2]["message"]["content"] = content
                cap.save()
                with self.assertRaises(nr.ReceiptError):
                    cap.validate()

    def test_missing_or_foreign_session_rejected_even_without_event_id(self):
        cap = Capture(self.root, "claude-cli")
        del cap.sources["stream"][0]["session_id"]
        cap.save()
        with self.assertRaises(nr.ReceiptError):
            cap.validate()
        for row in ({"type": "system", "sessionId": "foreign"},
                    {"type": "user", "sessionId": "unit-session"}, {"type": "system"}):
            with self.subTest(row=row):
                cap = Capture(self.root, "claude-cli")
                cap.sources["journal"].append(row)
                cap.save()
                with self.assertRaises(nr.ReceiptError):
                    cap.validate()

    def test_core_scope_changes_cannot_hide_behind_unchanged_input(self):
        cap = Capture(self.root)
        design = self.root / cap.data["scope"]["design"]
        design.write_text(design.read_text() + "Changed requirement\n", encoding="utf-8")
        with self.assertRaisesRegex(nr.ReceiptError, "Core review scope changed"):
            cap.validate()

    def test_legacy_placeholders_rejected(self):
        for value in nr.PLACEHOLDERS | {"UNAVAILABLE", "N/A"}:
            with self.subTest(value=value):
                text = f"Gemini：`session_id = sess`, `task_id = task`, `turn_id = {value}`"
                self.assertFalse(pl.has_full_receipt(text))
                self.assertIsNone(pl.extract_single_owner_receipt(text))


class NativeIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.ws = build_valid_complex_workspace(self.root, t3_author="Codex")
        self.scope = pl.compute_review_hash("t4", self.ws["accept"])
        self.c = Capture(self.root, "claude-cli", "t4", self.ws["plan"], self.scope, "claude")
        self.g = Capture(self.root, "antigravity-hub", "t4", self.ws["plan"], self.scope, "gemini")
        self.c_link = "[native-receipt](../../evidence/claude/receipt.json)"
        self.g_link = "[native-receipt](../../evidence/gemini/receipt.json)"
        self.replace_section(self.ws["t4"], 2, "2.", "Claude：" + self.c_link)
        self.replace_section(self.ws["t4"], 2, "3.", "Gemini：" + self.g_link)
        self.replace_section(self.ws["accept"], 3, "4.5", "- Claude：" + self.c_link + "\n- Gemini：" + self.g_link)

    @staticmethod
    def replace_section(path, level, prefix, text):
        doc = pl.parse_doc(path)
        section = pl.section_slice(doc, level, prefix, skip_fences=True)
        path.write_text(path.read_text("utf-8").replace(section, "\n" + text + "\n\n", 1), encoding="utf-8")

    def binding_issues(self):
        issues = []
        pl.verify_acceptance_t4_binding(pl.parse_doc(self.ws["accept"]), pl.parse_doc(self.ws["t4"]), issues)
        return issues

    def independence_issues(self, authors=None, sessions=None, mode="d4a"):
        issues = []
        pl.check_review_independence_and_roles(pl.parse_doc(self.ws["t4"]), "t4", authors or {"Codex"},
                                             sessions or {"author-session"}, [], mode, "PL-CP1-REV", issues)
        return issues

    def test_complete_pair_passes_real_cp1_path(self):
        self.assertEqual(self.binding_issues(), [])
        self.assertEqual(self.independence_issues(), [])
        rc, out, err = run_cli("check", str(self.ws["plan"]), "--gate", "cp1", "--json")
        self.assertEqual(rc, 0, out + err)

    def test_bad_native_link_cannot_fall_back_to_legacy(self):
        self.replace_section(self.ws["t4"], 2, "3.",
                             "Gemini：`session_id = ss`, `task_id = tk`, `turn_id = tr`\n"
                             "[native-receipt](../../missing.json)")
        self.assertIn("PL-REV-NATIVE", [i.code for i in self.independence_issues()])

    def test_same_author_and_same_session_still_rejected(self):
        for kwargs in ({"authors": {"Gemini"}}, {"sessions": {self.g.data["session_id"]}},
                       {"authors": {"Gemini"}, "mode": "d4b"}):
            with self.subTest(kwargs=kwargs):
                self.assertIn("PL-REV-INDEP", [i.code for i in self.independence_issues(**kwargs)])

    def test_native_and_text_id_conflict_rejected(self):
        self.replace_section(self.ws["t4"], 2, "3.", "Gemini：" + self.g_link + " `session_id = other-session`")
        self.assertIn("PL-REV-NATIVE", [i.code for i in self.independence_issues()])

    def test_native_prose_can_discuss_author_and_other_mcp_channels(self):
        self.replace_section(self.ws["t4"], 2, "3.", "Gemini：" + self.g_link +
                             " `session_id = gemini-session`，检查 Codex 编写的工具。\n"
                             "核查 verdantflare-agent-codex 和 verdantflare-agent-claude。")
        self.assertEqual(self.independence_issues(), [])

    def test_empty_author_sessions_fail_closed(self):
        issues = []
        pl.check_review_independence_and_roles(pl.parse_doc(self.ws["t4"]), "t4", {"Codex"}, set(),
                                             [], "d4a", "PL-CP1-REV", issues)
        self.assertIn("PL-REV-INDEP", [i.code for i in issues])
        changes = list((self.root / "docs/archive/changes").glob("*.md"))
        for path in changes:
            path.write_text(path.read_text().replace("session_id = sess-author-01", "missing evidence"))
        _, sessions, errors = pl.extract_stage_authors(pl.parse_doc(self.ws["plan"]), {3, 4})
        self.assertFalse(sessions)
        self.assertTrue(errors)

    def test_review_only_role_excluded_but_mixed_author_role_kept(self):
        path = self.root / "docs/archive/changes/change_20261115_t1-spec.md"
        original = path.read_text()
        for role, expected_author in (("轻量架构与考卷流程审阅", False), ("独立工程审", False),
                                      ("审阅与修改", True)):
            with self.subTest(role=role):
                path.write_text(original.replace("助手：Gemini（T1 方案编制）", f"助手：Gemini（T1 方案编制）、Claude（{role}）"))
                authors, _, _ = pl.extract_stage_authors(pl.parse_doc(self.ws["plan"]), {1})
                self.assertEqual("Claude" in authors, expected_author)

    def test_cp1_rechecks_bad_t1_and_t2_native_links(self):
        for stage in ("t1", "t2"):
            path = self.ws[stage]
            original = path.read_text()
            self.replace_section(path, 2, "3.", "Codex：[native-receipt](../../missing.json)")
            rc, out, _ = run_cli("check", str(self.ws["plan"]), "--gate", "cp1", "--json")
            self.assertEqual(rc, 1)
            self.assertIn("PL-REV-NATIVE", out)
            path.write_text(original)

    def test_scope_must_point_to_plan_acceptance_not_identical_copy(self):
        copied = self.root / "evidence/copied.md"
        copied.write_bytes(self.ws["accept"].read_bytes())
        self.g.data["scope"]["acceptance"] = "evidence/copied.md"
        self.g.write_descriptor()
        self.assertEqual(self.g.validate().agent, "Gemini")
        self.assertIn("PL-REV-NATIVE", [i.code for i in self.independence_issues()])

    def test_t4_missing_duplicate_swapped_unowned_or_mixed_receipts(self):
        contents = [
            "- Claude：" + self.c_link,
            "- Claude：" + self.c_link + "\n- Gemini：" + self.g_link + "\n- Gemini：" + self.g_link,
            "- Claude：" + self.g_link + "\n- Gemini：" + self.c_link,
            self.c_link + "\n- Gemini：" + self.g_link,
            "- Claude：" + self.c_link + "\n- Gemini：`session_id = ss`, `task_id = tk`, `turn_id = tr`",
        ]
        for content in contents:
            with self.subTest(content=content):
                self.replace_section(self.ws["accept"], 3, "4.5", content)
                self.assertIn("PL-REV-NATIVE", [i.code for i in self.binding_issues()])

    def test_t4_different_valid_receipt_rejected(self):
        other = Capture(self.root, "antigravity-hub", "t4", self.ws["plan"], self.scope, "other")
        self.assertEqual(other.validate().agent, "Gemini")
        self.replace_section(self.ws["accept"], 3, "4.5", "- Claude：" + self.c_link +
                             "\n- Gemini：[native-receipt](../../evidence/other/receipt.json)")
        self.assertIn("PL-REV-NATIVE", [i.code for i in self.binding_issues()])

    def test_stale_reviewed_input_blocks_gate(self):
        self.g.input.write_text("Changed after review", encoding="utf-8")
        rc, out, _ = run_cli("check", str(self.ws["plan"]), "--gate", "cp1", "--json")
        self.assertEqual(rc, 1)
        self.assertIn("PL-REV-NATIVE", out)


if __name__ == "__main__":
    unittest.main()
