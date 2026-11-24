"""Validate captured delegation evidence; never issue approval or run a gate.

The host must supply fresh native capture/readback separately from the policy's
historical evidence. These files are not cryptographic provider attestations.
Independent provider inspection and the actual technical gates remain required.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath
import re

from goal_evidence import GoalEvidence, validate_goal_evidence
from native_receipts import (
    ReceiptError, _json, _read_ref, digest, identifier, relative_file,
    text_digest, validate_receipt,
)

MAX_DOCUMENT = 256 * 1024
HEX = re.compile(r"[0-9a-f]{64}\Z")
ALLOWED_GATES = frozenset({"UI1", "CP0", "CP1"})
RETAINED_GATES = frozenset({"H1", "H2", "production", "new_cost"})


class PolicyError(ValueError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


def need(condition, code: str, message: str) -> None:
    if not condition:
        raise PolicyError(code, message)


def keys(value, expected: set[str]) -> None:
    need(isinstance(value, dict) and set(value) == expected,
         "PL-DELEGATE-SCHEMA", "Missing or unexpected policy fields")


def parse(raw: bytes) -> dict:
    need(isinstance(raw, bytes) and 0 < len(raw) <= MAX_DOCUMENT,
         "PL-DELEGATE-SCHEMA", "Policy document missing or oversized")
    try:
        result = _json(raw)
    except ReceiptError as exc:
        raise PolicyError("PL-DELEGATE-SCHEMA", "Invalid policy JSON") from exc
    need(isinstance(result, dict), "PL-DELEGATE-SCHEMA", "Policy must be an object")
    return result


def path_name(value: str) -> str:
    need(isinstance(value, str) and bool(value) and not any(c in value for c in ("\\", ":", "\x00")),
         "PL-DELEGATE-SCHEMA", "Invalid policy relative path")
    p = PurePosixPath(value)
    need(not p.is_absolute() and ".." not in p.parts and p.as_posix() == value and value != ".",
         "PL-DELEGATE-SCHEMA", "Noncanonical or escaping policy path")
    return value


def ref_shape(ref: dict) -> None:
    keys(ref, {"path", "sha256"})
    path_name(ref["path"])
    hash_shape(ref["sha256"])


def hash_shape(value) -> None:
    need(isinstance(value, str) and bool(HEX.fullmatch(value)),
         "PL-DELEGATE-SCHEMA", "Invalid policy SHA-256")


def read_ref(root: Path, ref: dict, code: str) -> bytes:
    ref_shape(ref)
    try:
        # Unlike media evidence, policy references may not escape via gDrive.
        target = relative_file(root, ref["path"])
        need(target.is_relative_to(root.resolve()), code, "Policy evidence escapes repository")
        return _read_ref(root, ref)
    except (ReceiptError, OSError) as exc:
        raise PolicyError(code, "Policy evidence missing or changed") from exc


def identifiers(values) -> tuple[str, ...]:
    need(isinstance(values, list) and bool(values), "PL-DELEGATE-SCHEMA", "Missing policy authors")
    try:
        for item in values:
            identifier(item)
        need(len(set(values)) == len(values), "PL-DELEGATE-SCHEMA", "Duplicate author session")
    except (ReceiptError, TypeError) as exc:
        raise PolicyError("PL-DELEGATE-SCHEMA", "Invalid policy author session") from exc
    return tuple(values)


@dataclass(frozen=True)
class PolicyEvidence:
    """Integrity metadata only; not an authorization or a CP0/CP1 verdict."""
    policy_id: str
    plan_id: str
    policy_sha256: str
    allowed_gates: tuple[str, ...]
    retained_gates: tuple[str, ...]
    reviewer_session: str
    reviewer_receipt_sha256: str
    current_goal: GoalEvidence
    reviewed_history_sha256: str
    final_decision: object | None = None


def policy_shape(payload: dict, *, repository_id: str, plan_id: str, plan_path: str) -> None:
    keys(payload, {"schema_version", "policy_id", "repository_id", "plan_id", "plan_path",
                   "archive_plan_path", "author_sessions", "authorization", "reviewed_user_history",
                   "allowed_gates", "retained_gates"})
    need(type(payload["schema_version"]) is int and payload["schema_version"] == 1,
         "PL-DELEGATE-SCHEMA", "Unknown policy version")
    for key in ("policy_id", "plan_id", "repository_id"):
        need(isinstance(payload[key], str) and bool(payload[key].strip()) and payload[key] == payload[key].strip(),
             "PL-DELEGATE-SCHEMA", "Invalid policy identity")
    active = path_name(payload["plan_path"])
    archived = path_name(payload["archive_plan_path"])
    need(PurePosixPath(active).parent.as_posix() == "docs/plan" and active.endswith(".plan.md")
         and archived == "docs/archive/plan/" + PurePosixPath(active).name,
         "PL-DELEGATE-BINDING", "Plan archive mapping is not isomorphic")
    need(bool(repository_id) and payload["repository_id"] == repository_id
         and payload["plan_id"] == plan_id and plan_path in {active, archived},
         "PL-DELEGATE-BINDING", "Policy belongs to another repository or plan")
    identifiers(payload["author_sessions"])
    for field, permitted in (("allowed_gates", ALLOWED_GATES), ("retained_gates", RETAINED_GATES)):
        values = payload[field]
        need(isinstance(values, list) and bool(values) and all(isinstance(v, str) for v in values),
             "PL-DELEGATE-SCHEMA", "Invalid policy gate collection")
        need(len(values) == len(set(values)) and set(values) <= permitted,
             "PL-DELEGATE-BINDING", "Unknown, duplicate or nondelegable gate")
    need(set(payload["retained_gates"]) == RETAINED_GATES,
         "PL-DELEGATE-BINDING", "Required human and release gates must be retained")
    hash_shape(payload["reviewed_user_history"])
    auth = payload["authorization"]
    keys(auth, {"provider", "session_id", "event_sha256", "capture", "observation"})
    need(auth["provider"] == "codex-user-goal-v1", "PL-DELEGATE-SOURCE", "Unsupported authorization source")
    try:
        identifier(auth["session_id"])
    except ReceiptError as exc:
        raise PolicyError("PL-DELEGATE-SCHEMA", "Invalid authorization session") from exc
    hash_shape(auth["event_sha256"])
    ref_shape(auth["capture"])
    ref_shape(auth["observation"])


def _goal(payload: dict, capture: bytes, observation: bytes) -> GoalEvidence:
    auth = payload["authorization"]
    try:
        return validate_goal_evidence(
            capture, observation, session_id=auth["session_id"],
            authorization_event_sha256=auth["event_sha256"],
            user_history_sha256=payload["reviewed_user_history"], observation_sha256=digest(observation))
    except ReceiptError as exc:
        raise PolicyError("PL-DELEGATE-SOURCE", "Goal source is changed, inactive or incomplete") from exc


def validate_policy_evidence(root: Path, policy_ref: dict, envelope_ref: dict, *,
                             repository_id: str, plan_id: str, plan_path: str,
                             current_capture: bytes, current_observation: bytes,
                             current_scope: dict, current_review_hash: str,
                             author_sessions: set[str], final_review: dict | None = None,
                             conversation_capture: bytes = b"") -> PolicyEvidence:
    """Check immutable policy, independent scope review, and supplied live source.

    Callers must derive context from the actual plan/provider, not this payload.
    This does not obtain live sources, authenticate local files, authorize H1/H2,
    or validate any process-execution receipt. Never use it alone to pass a gate.
    """
    raw = read_ref(root, policy_ref, "PL-DELEGATE-BINDING")
    payload = parse(raw)
    policy_shape(payload, repository_id=repository_id, plan_id=plan_id, plan_path=plan_path)
    need(isinstance(author_sessions, set) and bool(author_sessions)
         and all(isinstance(v, str) and v for v in author_sessions)
         and author_sessions == set(payload["author_sessions"]),
         "PL-DELEGATE-BINDING", "Actual author sessions are missing or differ")
    auth = payload["authorization"]
    historical_capture = read_ref(root, auth["capture"], "PL-DELEGATE-SOURCE")
    historical_observation = read_ref(root, auth["observation"], "PL-DELEGATE-SOURCE")
    _goal(payload, historical_capture, historical_observation)
    final_decision = None
    if final_review is not None:
        from final_decisions import validate_final_decision
        need(final_review.get("authorization_sha256") == auth["event_sha256"]
             and final_review.get("reviewed_history_sha256") == payload["reviewed_user_history"],
             "PL-DELEGATE-H2", "Final review is bound to another authorization history")
        final_decision = validate_final_decision(
            final_review, conversation_capture, current_capture, current_observation,
            session_id=auth["session_id"], repository_id=repository_id, plan_id=plan_id, plan_path=plan_path)
        # The complete CURRENT capture was validated above. This separately
        # proves continuity of the original reviewed scope; it is not a new
        # current readback and must never grant additional process permissions.
        _goal(payload, final_decision.reviewed_prefix, current_observation)
        current = final_decision.current_goal
    else:
        current = _goal(payload, current_capture, current_observation)
    need(current_capture.startswith(historical_capture), "PL-DELEGATE-SOURCE",
         "Current capture must preserve the complete historical byte prefix")
    need(_json(current_observation)["goal"]["updatedAt"] >= _json(historical_observation)["goal"]["updatedAt"],
         "PL-DELEGATE-SOURCE", "Current goal observation predates reviewed observation")
    envelope = parse(read_ref(root, envelope_ref, "PL-DELEGATE-REVIEW"))
    keys(envelope, {"schema_version", "policy_sha256", "receipt"})
    need(type(envelope["schema_version"]) is int and envelope["schema_version"] == 1,
         "PL-DELEGATE-SCHEMA", "Unknown policy envelope version")
    need(envelope["policy_sha256"] == digest(raw), "PL-DELEGATE-BINDING", "Envelope policy mismatch")
    receipt_raw = read_ref(root, envelope["receipt"], "PL-DELEGATE-REVIEW")
    desc = parse(receipt_raw)
    need(desc.get("scope") == current_scope, "PL-DELEGATE-REVIEW", "Scope review selected other core documents")
    try:
        verified = validate_receipt(relative_file(root, envelope["receipt"]["path"]), root,
                                    plan_path=plan_path, stage="t2", role="detail",
                                    review_hash=current_review_hash)
        need(verified.sha256 == digest(receipt_raw), "PL-DELEGATE-REVIEW",
             "Native receipt changed between envelope read and validation")
        need(verified.session_id not in author_sessions | {auth["session_id"]},
             "PL-DELEGATE-REVIEW", "Policy reviewer is an author or authorization session")
        required = {policy_ref["path"]: policy_ref["sha256"],
                    auth["capture"]["path"]: auth["capture"]["sha256"],
                    auth["observation"]["path"]: auth["observation"]["sha256"]}
        need(len(required) == 3, "PL-DELEGATE-BINDING", "Policy and source files must be distinct")
        inputs = {ref["path"]: ref["sha256"] for ref in desc["inputs"]}
        need(all(inputs.get(p) == h for p, h in required.items()),
             "PL-DELEGATE-REVIEW", "Scope reviewer did not consume the policy and its source evidence")
        # v1 detail reviews are native Antigravity Hub responses. Re-read the
        # hash-bound source; do not trust an unattached verdict in the envelope.
        source = _json(read_ref(root, desc["sources"]["transcript"], "PL-DELEGATE-REVIEW"))
        response = source["steps"][-1]["content"]
        need(text_digest(response) == desc["response_sha256"],
             "PL-DELEGATE-REVIEW", "Scope review response changed")
        verdict = _json(response.encode("utf-8"))
    except (ReceiptError, KeyError, TypeError, AttributeError, OSError) as exc:
        raise PolicyError("PL-DELEGATE-REVIEW", "Invalid native policy review") from exc
    expected = {"schema_version": 1, "verdict": "scope-confirmed", "policy_sha256": digest(raw),
                "plan_id": plan_id, "authorization_sha256": auth["event_sha256"],
                "reviewed_history_sha256": payload["reviewed_user_history"],
                "provider_readback": True, "retained_gates": sorted(RETAINED_GATES)}
    need(isinstance(verdict, dict) and verdict == expected
         and type(verdict.get("schema_version")) is int and verdict.get("provider_readback") is True,
         "PL-DELEGATE-REVIEW", "Native reviewer did not confirm this exact policy and retained gates")
    return PolicyEvidence(payload["policy_id"], plan_id, digest(raw), tuple(payload["allowed_gates"]),
                          tuple(payload["retained_gates"]), verified.session_id, verified.sha256, current,
                          payload["reviewed_user_history"], final_decision)
