import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("validate_preparation", ROOT / "scripts/validate_preparation.py")
validator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(validator)


def uid(n):
    return f"00000000-0000-7000-8000-{n:012x}"


def fixture():
    ref = {"store_id": uid(4), "artifact_id": uid(5), "version_id": uid(6)}
    record = {
        "schema_version": 1, "kind": "short_drama_generation_preparation",
        "project_id": uid(1), "basis_revision_id": uid(2), "stage": "materials",
        "scope": {
            "targets": [{"kind": "location", "key": "LOC06", "state_key": "S17", "purpose": "scene_look"}],
            "instruction": "Synthetic fixture only", "execution": None,
            "max_calls": 0, "max_retries": 0, "max_tokens": None, "cost_limit": None,
        },
        "basis": [{"file_id": uid(3), "content_ref": ref, "purpose": "lighting"}],
        "coverage": {key: {"state": "not_required", "note": "Synthetic validation only"}
                     for key in ("character", "wardrobe", "prop", "location", "sound", "music")},
        "decision": {"state": "pending", "scope_sha256": "0" * 64},
    }
    record["decision"]["scope_sha256"] = validator.scope_digest(record)
    opened = {"structuredContent": {"project_id": uid(1), "revision_id": uid(2),
               "manifest": {"project_id": uid(1), "files": [{"file_id": uid(3), "content_ref": ref}]}}}
    return record, opened


class PreparationTests(unittest.TestCase):
    def test_pending_can_be_archived_but_not_handed_off(self):
        record, opened = fixture()
        result = validator.evaluate(record, opened)
        self.assertTrue(result["valid"])
        self.assertTrue(result["basis_verified"])
        self.assertFalse(result["handoff_record_complete"])
        self.assertFalse(result["execution_authorized"])

    def test_all_six_categories_required(self):
        for kind in ("character", "wardrobe", "prop", "location", "sound", "music"):
            record, opened = fixture()
            del record["coverage"][kind]
            self.assertFalse(validator.evaluate(record, opened)["valid"], kind)

    def test_project_revision_and_content_must_match(self):
        for field in ("project_id", "revision_id", "content_ref"):
            record, opened = fixture()
            opened = copy.deepcopy(opened)
            result = opened["structuredContent"]
            if field == "content_ref":
                result["manifest"]["files"][0]["content_ref"]["version_id"] = uid(99)
            else:
                result[field] = uid(99)
            self.assertFalse(validator.evaluate(record, opened)["valid"], field)

    def test_scope_changes_invalidate_decision(self):
        record, opened = fixture()
        record["scope"]["max_calls"] = 10
        self.assertIn("SCOPE_DIGEST_MISMATCH", validator.evaluate(record, opened)["errors"])

    def test_approval_requires_evidence(self):
        record, opened = fixture()
        record["decision"]["state"] = "approved"
        self.assertFalse(validator.evaluate(record, opened)["valid"])

    def test_even_complete_self_reported_decision_is_not_authorization(self):
        record, opened = fixture()
        record["scope"].update(max_calls=1, execution={"service": "test", "model": "fixture", "route": "fake"})
        record["decision"] = {"state": "approved", "scope_sha256": validator.scope_digest(record),
            "evidence": {"source": "user_message", "reference": "synthetic-only", "summary": "Not a real approval"}}
        result = validator.evaluate(record, opened)
        self.assertTrue(result["handoff_record_complete"])
        self.assertFalse(result["execution_authorized"])
        self.assertEqual(result["authority_check"], "requires_trusted_consumer")

    def test_missing_open_never_verifies_basis(self):
        record, _ = fixture()
        self.assertIn("FIXED_BASIS_NOT_VERIFIED", validator.evaluate(record)["blockers"])

    def test_retry_budget_part_of_total(self):
        record, opened = fixture()
        record["scope"].update(max_calls=1, max_retries=1)
        record["decision"]["scope_sha256"] = validator.scope_digest(record)
        self.assertIn("RETRIES_EXCEED_TOTAL_CALLS", validator.evaluate(record, opened)["errors"])

    def test_zero_budget_and_paused_stay_blocked(self):
        record, opened = fixture()
        record["scope"].update(max_calls=1, max_tokens=0, cost_limit={"unit": "CNY", "amount": 0})
        record["decision"] = {"state": "paused", "scope_sha256": validator.scope_digest(record),
            "evidence": {"source": "studio_review", "reference": "synthetic", "summary": "Synthetic pause"}}
        result = validator.evaluate(record, opened)
        for code in ("HUMAN_DECISION_REQUIRED", "ZERO_TOKEN_ALLOWANCE", "ZERO_COST_ALLOWANCE"):
            self.assertIn(code, result["blockers"])

    def test_cli_pending_is_nonzero_when_handoff_requested(self):
        record, opened = fixture()
        with tempfile.TemporaryDirectory() as tmp:
            record_path, open_path = Path(tmp) / "record.json", Path(tmp) / "open.json"
            record_path.write_text(json.dumps(record))
            open_path.write_text(json.dumps(opened))
            command = [sys.executable, str(ROOT / "scripts/validate_preparation.py"), str(record_path),
                       "--project-open", str(open_path), "--check-handoff"]
            result = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(result.returncode, 2)
            self.assertFalse(json.loads(result.stdout)["execution_authorized"])

    def test_rpc_error_is_not_a_valid_manifest(self):
        record, _ = fixture()
        self.assertFalse(validator.evaluate(record, {"isError": True})["valid"])

    def test_ambiguous_json_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "invalid.json"
            for text in ('{"state":"pending","state":"approved"}', '{"budget":NaN}'):
                path.write_text(text)
                with self.assertRaises(ValueError):
                    validator.load_json(path)


if __name__ == "__main__":
    unittest.main()
