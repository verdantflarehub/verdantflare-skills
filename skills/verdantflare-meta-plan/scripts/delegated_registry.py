"""Load explicit per-repository policy references; never activate a gate."""
from dataclasses import dataclass
from pathlib import Path

from delegated_policy import (MAX_DOCUMENT, PolicyError, keys, need, parse,
                              path_name, policy_shape, read_ref, ref_shape)

REGISTRY_PATH = "docs/plan/.delegated-policies.json"


@dataclass(frozen=True)
class Registration:
    policy_id: str
    plan_id: str
    plan_path: str
    archive_plan_path: str
    policy: dict
    envelope: dict


def load_registry(root: Path, *, repository_id: str) -> tuple[Registration, ...]:
    """repository_id must be derived from the actual Git repo by the caller."""
    source = root / REGISTRY_PATH
    if not source.exists() and not source.is_symlink():
        return ()
    try:
        need(source.resolve(strict=True).is_relative_to(root.resolve()),
             "PL-DELEGATE-BINDING", "Registry escapes repository")
        with source.open("rb") as stream:
            raw = stream.read(MAX_DOCUMENT + 1)
    except (OSError, RuntimeError):
        raise PolicyError("PL-DELEGATE-BINDING", "Registry is unavailable") from None
    registry = parse(raw)
    keys(registry, {"schema_version", "repository_id", "policies"})
    need(type(registry["schema_version"]) is int and registry["schema_version"] == 1,
         "PL-DELEGATE-SCHEMA", "Unknown registry version")
    need(isinstance(repository_id, str) and bool(repository_id)
         and registry["repository_id"] == repository_id,
         "PL-DELEGATE-BINDING", "Registry repository differs")
    need(isinstance(registry["policies"], list), "PL-DELEGATE-SCHEMA", "Invalid registry policies")
    found = []; policy_ids = set(); plan_ids = set(); paths = set(); selectors = set()
    for entry in registry["policies"]:
        keys(entry, {"policy", "envelope"})
        ref_shape(entry["policy"]); ref_shape(entry["envelope"])
        payload = parse(read_ref(root, entry["policy"], "PL-DELEGATE-BINDING"))
        # Structural verification of every registered policy, before selection.
        policy_shape(payload, repository_id=repository_id,
                     plan_id=payload.get("plan_id"), plan_path=payload.get("plan_path"))
        pair = {payload["plan_path"], payload["archive_plan_path"]}
        selector = (payload["authorization"]["session_id"], payload["authorization"]["event_sha256"])
        need(payload["policy_id"] not in policy_ids and payload["plan_id"] not in plan_ids
             and not pair.intersection(paths) and selector not in selectors,
             "PL-DELEGATE-BINDING", "Duplicate registry policy, plan, path or source selector")
        envelope = parse(read_ref(root, entry["envelope"], "PL-DELEGATE-REVIEW"))
        keys(envelope, {"schema_version", "policy_sha256", "receipt"})
        need(type(envelope["schema_version"]) is int and envelope["schema_version"] == 1,
             "PL-DELEGATE-SCHEMA", "Unknown registry envelope version")
        need(envelope["policy_sha256"] == entry["policy"]["sha256"],
             "PL-DELEGATE-BINDING", "Registry envelope policy differs")
        ref_shape(envelope["receipt"])
        policy_ids.add(payload["policy_id"]); plan_ids.add(payload["plan_id"])
        paths.update(pair); selectors.add(selector)
        found.append(Registration(payload["policy_id"], payload["plan_id"], payload["plan_path"],
                                  payload["archive_plan_path"], dict(entry["policy"]), dict(entry["envelope"])))
    return tuple(found)


def select_registration(entries: tuple[Registration, ...], *, plan_id: str,
                        plan_path: str) -> Registration | None:
    path_name(plan_path)
    matches = [r for r in entries if r.plan_id == plan_id
               or plan_path in {r.plan_path, r.archive_plan_path}]
    if not matches:
        return None
    need(len(matches) == 1 and matches[0].plan_id == plan_id
         and plan_path in {matches[0].plan_path, matches[0].archive_plan_path},
         "PL-DELEGATE-BINDING", "Registered plan identity differs")
    return matches[0]
