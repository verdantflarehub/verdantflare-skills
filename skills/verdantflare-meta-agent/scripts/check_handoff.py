"""Read-only, dependency-free handoff preflight; no dispatch or authorization inference."""
from __future__ import annotations

import argparse
import json
from pathlib import Path, PureWindowsPath
import re
import sys

MODES = {
    "codex": {"plugin", "managed"},
    "claude": {"panel", "managed"},
    "antigravity": {"panel", "cli"},
    "grok": {"acp", "headless"},
}
FIELDS = {
    "plan_id", "plan_name", "plan_path", "task_key", "task_label", "change_path",
    "assignment_id", "revision", "sender", "recipient", "reply_to", "goal",
    "inputs", "allowed_write_paths", "constraints", "deliverables", "verification",
    "completion_criteria", "authorization", "closeout_owner",
}
OPTIONAL_FIELDS = {"review_stage", "review_doc_path"}
ENDPOINT_FIELDS = {"session_key", "service", "agent", "mode", "native_session_id", "generation"}
REVIEW_STAGES = {"claude_arch", "codex_detail", "claude_escalation", "claude_arch_audit", "acceptance_audit"}
CLAUDE_CHAR_CAPS = {
    "claude_escalation": 800,
    "claude_arch_audit": 1000,
    "claude_arch": 1200,
}
RD_REVIEW_DOC_RE = re.compile(
    r"^docs/reviews/review_\d{8}_[a-z0-9.-]+_(?:t1|t2|t4)(?:-r(?:[2-9]|[1-9]\d+))?\.md$"
)
ACCEPTANCE_DOC_RE = re.compile(r"^docs/acceptance/accept_\d{8}_[a-z0-9.-]+\.md$")
CLAUDE_PLAN_DOC_RE = re.compile(r"^docs/(?:archive/)?plan/[A-Za-z0-9._/-]+\.plan\.md$")
CLAUDE_CHANGE_DOC_RE = re.compile(r"^docs/(?:archive/)?changes/change_[A-Za-z0-9._-]+\.md$")
CLAUDE_INPUT_DOC_RE = re.compile(r"^docs/(?:design|plan|reviews|acceptance)/[A-Za-z0-9._/-]+\.md$")
CLAUDE_DELIVERABLE_DOC_RE = re.compile(r"^docs/(?:reviews|acceptance)/[A-Za-z0-9._/-]+\.md$")
ASCII_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,64}$")
ID_FIELDS = ("plan_id", "task_key", "assignment_id")


class InvalidHandoff(ValueError):
    pass


def text(value, label):
    if not isinstance(value, str) or not value.strip() or "\x00" in value or len(value) > 65536:
        raise InvalidHandoff(f"{label}: non-empty text required")
    return value


def exact(value, fields, label):
    if not isinstance(value, dict) or set(value) != fields:
        raise InvalidHandoff(f"{label}: incorrect field set")


def positive(value, label):
    if type(value) is not int or value < 1:
        raise InvalidHandoff(f"{label}: positive integer required")


def endpoint(value, label):
    exact(value, ENDPOINT_FIELDS, label)
    for key in ENDPOINT_FIELDS - {"generation"}:
        text(value[key], f"{label}.{key}")
    positive(value["generation"], f"{label}.generation")
    if value["agent"] not in MODES or value["mode"] not in MODES[value["agent"]]:
        raise InvalidHandoff(f"{label}: unsupported agent/mode")


def relative_file(root, value, label, *, exists=False):
    value = text(value, label)
    win = PureWindowsPath(value)
    if "\\" in value or win.drive or win.root or Path(value).is_absolute() or ":" in value:
        raise InvalidHandoff(f"{label}: workspace-relative forward-slash path required")
    if any(part in {"..", ""} for part in value.split("/")):
        raise InvalidHandoff(f"{label}: traversal or empty path component")
    try:
        target = (root / value).resolve()
        target.relative_to(root)
    except (OSError, ValueError, RuntimeError):
        raise InvalidHandoff(f"{label}: path escapes workspace or cannot resolve") from None
    if exists and not target.is_file():
        raise InvalidHandoff(f"{label}: referenced file missing")
    return target


def _total_text_chars(node) -> int:
    """Count every string value recursively across the entire handoff payload (zero uncounted string arrays or path fields)."""
    if isinstance(node, str):
        return len(node)
    if isinstance(node, list):
        return sum(_total_text_chars(item) for item in node)
    if isinstance(node, dict):
        return sum(_total_text_chars(val) for val in node.values())
    return 0


def _validate_review_and_claude_scope(payload, root: Path) -> None:
    present_opt = set(payload) & OPTIONAL_FIELDS
    if present_opt and present_opt != OPTIONAL_FIELDS:
        raise InvalidHandoff("handoff: review_stage and review_doc_path must be provided as a pair")

    review_stage = None
    review_doc_path = None
    if present_opt:
        review_stage = text(payload["review_stage"], "review_stage")
        if review_stage not in REVIEW_STAGES:
            raise InvalidHandoff("review_stage: unsupported review_stage enum")
        review_doc_path = text(payload["review_doc_path"], "review_doc_path")
        relative_file(root, review_doc_path, "review_doc_path")
        if review_stage == "acceptance_audit":
            if not ACCEPTANCE_DOC_RE.match(review_doc_path):
                raise InvalidHandoff("review_doc_path: acceptance_audit requires docs/acceptance/accept_YYYYMMDD_<topic>.md")
        elif not RD_REVIEW_DOC_RE.match(review_doc_path):
            raise InvalidHandoff("review_doc_path: stage requires docs/reviews/review_YYYYMMDD_<topic>_<t1|t2|t4>[-rN].md")

    recipient_is_claude = payload["recipient"]["agent"].lower() == "claude"
    stage_is_claude = review_stage in CLAUDE_CHAR_CAPS

    if recipient_is_claude and not stage_is_claude:
        raise InvalidHandoff(
            "HO-CLAUDE-SCOPE: recipient claude requires review_stage in claude_arch|claude_escalation|claude_arch_audit"
        )
    if stage_is_claude and not recipient_is_claude:
        raise InvalidHandoff("HO-CLAUDE-SCOPE: claude_* review_stage must target recipient agent claude")

    if recipient_is_claude:
        if payload["allowed_write_paths"]:
            raise InvalidHandoff("HO-CLAUDE-SCOPE: claude handoff must be read-only (allowed_write_paths must be [])")
        if not CLAUDE_PLAN_DOC_RE.match(payload["plan_path"]):
            raise InvalidHandoff("HO-CLAUDE-SCOPE: plan_path restricted to docs/[archive/]plan/*.plan.md")
        if not CLAUDE_CHANGE_DOC_RE.match(payload["change_path"]):
            raise InvalidHandoff("HO-CLAUDE-SCOPE: change_path restricted to docs/[archive/]changes/change_*.md")
        for index, item in enumerate(payload["inputs"]):
            if not CLAUDE_INPUT_DOC_RE.match(item):
                raise InvalidHandoff(
                    f"HO-CLAUDE-SCOPE: inputs[{index}] restricted to docs/{{design,plan,reviews,acceptance}}/**/*.md"
                )
        for index, item in enumerate(payload["deliverables"]):
            if not CLAUDE_DELIVERABLE_DOC_RE.match(item):
                raise InvalidHandoff(
                    f"HO-CLAUDE-SCOPE: deliverables[{index}] restricted to docs/{{reviews,acceptance}}/**/*.md"
                )
        for key in ID_FIELDS:
            if not ASCII_ID_RE.match(payload[key]):
                raise InvalidHandoff(f"HO-CLAUDE-SCOPE: {key} must match ASCII identifier [A-Za-z0-9._:-]{{1,64}}")
        for ep_key in ("sender", "recipient", "reply_to"):
            for sub in ("session_key", "service", "native_session_id"):
                if not ASCII_ID_RE.match(payload[ep_key][sub]):
                    raise InvalidHandoff(
                        f"HO-CLAUDE-SCOPE: {ep_key}.{sub} must match ASCII identifier [A-Za-z0-9._:-]{{1,64}}"
                    )
        cap = CLAUDE_CHAR_CAPS[review_stage]
        total_chars = _total_text_chars(payload)
        if total_chars > cap:
            raise InvalidHandoff(
                f"HO-CLAUDE-SCOPE: total_text_chars ({total_chars}) exceeds {review_stage} limit ({cap})"
            )


def validate(payload, workspace):
    root = Path(workspace).resolve()
    if not root.is_dir():
        raise InvalidHandoff("workspace: directory required")
    if not isinstance(payload, dict):
        raise InvalidHandoff("handoff: incorrect field set")
    keys = set(payload)
    unknown = keys - (FIELDS | OPTIONAL_FIELDS)
    missing = FIELDS - keys
    if unknown or missing:
        raise InvalidHandoff("handoff: incorrect field set")

    scalar = FIELDS - {
        "revision", "sender", "recipient", "reply_to", "inputs",
        "allowed_write_paths", "constraints", "deliverables", "verification", "completion_criteria",
    }
    for key in scalar:
        text(payload[key], key)
    positive(payload["revision"], "revision")
    for key in ("sender", "recipient", "reply_to"):
        endpoint(payload[key], key)
    seen = {}
    for key in ("sender", "recipient", "reply_to"):
        ep = payload[key]
        if ep["session_key"] in seen and seen[ep["session_key"]] != ep:
            raise InvalidHandoff("endpoints: conflicting identity for same session_key")
        seen[ep["session_key"]] = ep
    if payload["sender"]["session_key"] == payload["recipient"]["session_key"]:
        raise InvalidHandoff("recipient: delegation must target another session")
    for key in ("plan_path", "change_path"):
        relative_file(root, payload[key], key, exists=True)
    for key in ("inputs", "allowed_write_paths", "constraints", "deliverables", "verification", "completion_criteria"):
        values = payload[key]
        if not isinstance(values, list) or (not values and key not in {"inputs", "allowed_write_paths"}):
            raise InvalidHandoff(f"{key}: list required; outcome and constraints lists cannot be empty")
        for index, value in enumerate(values):
            label = f"{key}[{index}]"
            if key == "inputs":
                relative_file(root, value, label, exists=True)
            elif key in {"allowed_write_paths", "deliverables"}:
                relative_file(root, value, label)
            else:
                text(value, label)

    _validate_review_and_claude_scope(payload, root)

    result = {
        "valid": True,
        "plan_id": payload["plan_id"],
        "task_key": payload["task_key"],
        "assignment_id": payload["assignment_id"],
        "revision": payload["revision"],
        "checks": "fields-and-local-paths-only",
    }
    if "review_stage" in payload:
        result["review_stage"] = payload["review_stage"]
        result["review_doc_path"] = payload["review_doc_path"]
    return result


def unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise InvalidHandoff("JSON: duplicate key")
        result[key] = value
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--input", required=True)
    args = parser.parse_args()
    try:
        raw = Path(args.input).read_text(encoding="utf-8")
        if len(raw.encode("utf-8")) > 262144:
            raise InvalidHandoff("JSON: handoff exceeds 256 KiB")
        result = validate(json.loads(raw, object_pairs_hook=unique_pairs), args.workspace)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (InvalidHandoff, OSError, UnicodeError, json.JSONDecodeError) as error:
        # Never reflect JSON content, absolute paths, or arbitrary exception strings.
        message = str(error) if isinstance(error, InvalidHandoff) else "input: cannot read valid UTF-8 JSON"
        print(json.dumps({"valid": False, "error": message}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
