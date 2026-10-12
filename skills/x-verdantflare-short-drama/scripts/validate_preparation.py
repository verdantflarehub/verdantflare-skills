#!/usr/bin/env python3
"""Validate a fixed preparation record. Never authorizes or submits generation."""
import argparse
import hashlib
import json
from pathlib import Path

from jsonschema import Draft202012Validator


SCHEMA_PATH = Path(__file__).resolve().parents[1] / "references/production/generation-preparation.schema.json"
SCOPE_KEYS = ("schema_version", "kind", "project_id", "basis_revision_id", "stage", "scope", "basis", "coverage")


def scope_digest(record):
    data = {key: record[key] for key in SCOPE_KEYS}
    canonical = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _unique_pairs(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON key")
        value[key] = item
    return value


def _reject_constant(value):
    raise ValueError("non-finite JSON number")


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"),
                      object_pairs_hook=_unique_pairs, parse_constant=_reject_constant)


def evaluate(record, opened=None):
    schema = load_json(SCHEMA_PATH)
    errors = ["SCHEMA: " + "/".join(map(str, error.absolute_path))
              for error in Draft202012Validator(schema).iter_errors(record)]
    verified = False
    blockers = []
    if not errors:
        if scope_digest(record) != record["decision"]["scope_sha256"]:
            errors.append("SCOPE_DIGEST_MISMATCH")
        scope = record["scope"]
        if scope["max_retries"] > max(0, scope["max_calls"] - 1):
            errors.append("RETRIES_EXCEED_TOTAL_CALLS")
        if opened is not None:
            if not isinstance(opened, dict) or opened.get("isError") or opened.get("error"):
                errors.append("PROJECT_OPEN_ERROR")
            else:
                result = opened.get("structuredContent", opened)
                manifest = result.get("manifest") if isinstance(result, dict) else None
                if (not isinstance(manifest, dict)
                        or result.get("project_id") != record["project_id"]
                        or manifest.get("project_id") != record["project_id"]
                        or result.get("revision_id") != record["basis_revision_id"]):
                    errors.append("PROJECT_OR_REVISION_MISMATCH")
                elif not isinstance(manifest.get("files"), list):
                    errors.append("MANIFEST_FILES_INVALID")
                else:
                    files = manifest["files"]
                    ids = [file.get("file_id") for file in files if isinstance(file, dict)]
                    if len(ids) != len(files) or len(set(ids)) != len(ids):
                        errors.append("MANIFEST_FILES_INVALID")
                    else:
                        refs = {file["file_id"]: file.get("content_ref") for file in files}
                        for basis in record["basis"]:
                            if refs.get(basis["file_id"]) != basis["content_ref"]:
                                errors.append("BASIS_REFERENCE_MISMATCH: " + basis["file_id"])
                        verified = not errors
        if record["decision"]["state"] != "approved":
            blockers.append("HUMAN_DECISION_REQUIRED")
        if scope["execution"] is None:
            blockers.append("EXECUTION_UNRESOLVED")
        if scope["max_calls"] <= 0:
            blockers.append("NO_CALL_ALLOWANCE")
        if scope["max_tokens"] == 0:
            blockers.append("ZERO_TOKEN_ALLOWANCE")
        if scope["cost_limit"] is not None and scope["cost_limit"]["amount"] == 0:
            blockers.append("ZERO_COST_ALLOWANCE")
    if not verified:
        blockers.append("FIXED_BASIS_NOT_VERIFIED")
    return {
        "valid": not errors,
        "basis_verified": verified,
        "handoff_record_complete": not errors and not blockers,
        "execution_authorized": False,
        "authority_check": "requires_trusted_consumer",
        "budget_enforcement": "not_implemented_in_WF1",
        "errors": errors,
        "blockers": blockers,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("record", type=Path)
    parser.add_argument("--project-open", type=Path, help="Saved response for the fixed basis revision")
    parser.add_argument("--check-handoff", action="store_true", help="Fail if decision, scope or basis is incomplete")
    args = parser.parse_args()
    try:
        result = evaluate(load_json(args.record), load_json(args.project_open) if args.project_open else None)
    except (OSError, ValueError, TypeError, KeyError, OverflowError):
        result = {"valid": False, "execution_authorized": False, "errors": ["INVALID_INPUT"]}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not result["valid"]:
        return 1
    return 2 if args.check_handoff and not result["handoff_record_complete"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
