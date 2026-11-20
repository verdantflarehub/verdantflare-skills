"""Content-addressed local gate execution records, not signed attestations.

Only the fixed checker is executed. Consumption always requires a separately
derived current state and fresh technical/source validation by plan_lint.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import signal
import subprocess
import sys
import tempfile
import time
import uuid

from delegated_policy import PolicyError, MAX_DOCUMENT, keys, need, parse, read_ref, ref_shape

STORE = "docs/acceptance/process"
VERSION = "delegated-process-v1"
CODE = "PL-DELEGATE-RECEIPT"
TIMEOUT = 150


def canonical(value) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                       allow_nan=False) + "\n").encode("utf-8")


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def tool_fingerprint() -> dict:
    folder = Path(__file__).resolve().parent
    return {"version": VERSION, "files": {
        p.name: sha(p.read_bytes()) for p in sorted(folder.glob("*.py")) if p.is_file()
    }}


def command_args(plan_path: str, gate: str) -> list[str]:
    need(gate in {"CP0", "CP1"}, CODE, "Unsupported process gate")
    return ["check", plan_path, "--gate", gate.lower(), "--indep-mode", "d4a", "--json"]


def store_bytes(root: Path, raw: bytes) -> dict:
    """Create by digest, never replace an existing file or follow a symlink."""
    need(0 < len(raw) <= MAX_DOCUMENT, CODE, "Process record is missing or oversized")
    digest = sha(raw)
    rel = f"{STORE}/{digest}.json"
    target = root.resolve() / rel
    try:
        # Reject existing symbolic directory components before creating anything.
        parent = root.resolve()
        for component in Path(STORE).parts:
            parent = parent / component
            need(not parent.is_symlink(), CODE, "Process store has a symbolic directory")
            parent.mkdir(exist_ok=True)
        need(not target.is_symlink(), CODE, "Process record is a symbolic link")
        try:
            fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            with target.open("rb") as stream:
                need(stream.read(MAX_DOCUMENT + 1) == raw, CODE, "Immutable process record differs")
        else:
            with os.fdopen(fd, "wb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
        return {"path": rel, "sha256": digest}
    except OSError:
        raise PolicyError(CODE, "Cannot persist process record") from None


def content_ref(path: str) -> dict:
    prefix = STORE + "/"
    need(isinstance(path, str) and path.startswith(prefix) and path.endswith(".json"),
         CODE, "Process reference is outside the content-addressed store")
    result = {"path": path, "sha256": path[len(prefix):-5]}
    ref_shape(result)
    need(path == f"{STORE}/{result['sha256']}.json", CODE, "Invalid process record path")
    return result


def read_content(root: Path, ref: dict) -> bytes:
    ref_shape(ref)
    need(content_ref(ref["path"]) == ref, CODE, "Process filename and digest differ")
    raw = read_ref(root, ref, CODE)
    need(len(raw) <= MAX_DOCUMENT, CODE, "Process record is oversized")
    return raw


def validate_success(raw: bytes, *, plan_path: str, gate: str) -> None:
    result = parse(raw)
    expected = {"ok": True, "exit_code": 0, "gate": gate.lower(), "plan": plan_path,
                "codes": [], "issues": []}
    need(result == expected and result.get("ok") is True and type(result.get("exit_code")) is int,
         CODE, "Checker output is not an exact successful gate result")


def observe_check(root: Path, state: dict) -> tuple[bytes, dict]:
    """Observe the actual child return code; no shell or policy-owned commands."""
    args = command_args(state["plan_path"], state["gate"])
    command = [sys.executable, str(Path(__file__).resolve().with_name("plan_lint.py")), *args]
    started = datetime.now(timezone.utc).isoformat()
    start = time.monotonic_ns()
    try:
        with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
            # TemporaryFile is an OS-managed stream, never a repository artifact.
            proc = subprocess.Popen(command, cwd=root, stdout=stdout, stderr=stderr,
                                    stdin=subprocess.DEVNULL, start_new_session=os.name != "nt",
                                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            try:
                returncode = proc.wait(timeout=TIMEOUT)
            except BaseException:
                # Only the tree created for this check. Provider grandchildren
                # must not survive a timed-out checker or a coordinator interrupt.
                if os.name == "nt":
                    try:
                        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                       timeout=10, check=False)
                    finally:
                        if proc.poll() is None:
                            proc.kill()
                else:
                    try:
                        os.killpg(proc.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                proc.wait(timeout=5)
                raise
            stdout.seek(0); stderr.seek(0)
            output = stdout.read(MAX_DOCUMENT + 1)
            error = stderr.read(MAX_DOCUMENT + 1)
    except (OSError, subprocess.TimeoutExpired):
        raise PolicyError(CODE, "Gate process failed to start or timed out") from None
    need(returncode == 0 and not error and len(output) <= MAX_DOCUMENT,
         CODE, "Gate process did not complete successfully")
    validate_success(output, plan_path=state["plan_path"], gate=state["gate"])
    observation = {
        "schema_version": 1, "provider": "plan-lint-subprocess-v1", "run_id": str(uuid.uuid4()),
        "started_at": started, "finished_at": datetime.now(timezone.utc).isoformat(),
        "duration_ns": time.monotonic_ns() - start, "exit_code": returncode,
        "argv_sha256": sha(canonical(args)), "state_sha256": sha(canonical(state)),
        "python_version": platform.python_version(),
        "markdown_it_version": importlib.metadata.version("markdown-it-py"),
    }
    return output, observation


def persist_run(root: Path, state: dict, output: bytes, observation: dict) -> dict:
    """Called only after real observation and final state/source comparison."""
    validate_success(output, plan_path=state["plan_path"], gate=state["gate"])
    need(observation.get("state_sha256") == sha(canonical(state)), CODE, "Observed gate state differs")
    # Validate before touching the evidence store.
    validate_observation(observation, state)
    output_ref = store_bytes(root, output)
    observation_ref = store_bytes(root, canonical({**observation, "stdout": output_ref}))
    receipt = {"schema_version": 1, "kind": VERSION, "verdict": "technical-pass",
               "state": state, "observation": observation_ref}
    return store_bytes(root, canonical(receipt))


def validate_observation(obs: dict, state: dict) -> None:
    keys(obs, {"schema_version", "provider", "run_id", "started_at", "finished_at", "duration_ns",
               "exit_code", "argv_sha256", "state_sha256", "python_version", "markdown_it_version"})
    need(type(obs["schema_version"]) is int and obs["schema_version"] == 1
         and obs["provider"] == "plan-lint-subprocess-v1"
         and type(obs["exit_code"]) is int and obs["exit_code"] == 0,
         CODE, "Invalid successful process observation")
    try:
        need(str(uuid.UUID(obs["run_id"])) == obs["run_id"], CODE, "Invalid process run ID")
        start = datetime.fromisoformat(obs["started_at"])
        end = datetime.fromisoformat(obs["finished_at"])
        need(start.tzinfo is not None and end.tzinfo is not None and start <= end,
             CODE, "Invalid process timestamps")
    except (ValueError, TypeError, AttributeError):
        raise PolicyError(CODE, "Invalid process identity or timestamps") from None
    need(type(obs["duration_ns"]) is int and obs["duration_ns"] > 0
         and all(isinstance(obs[k], str) and bool(obs[k]) for k in ("python_version", "markdown_it_version")),
         CODE, "Missing process runtime observation")
    need(obs["argv_sha256"] == sha(canonical(command_args(state["plan_path"], state["gate"])))
         and obs["state_sha256"] == sha(canonical(state)), CODE, "Process command or state differs")


def validate_run(root: Path, ref: dict, expected_state: dict) -> dict:
    """Verify byte bindings; the caller must ALSO rerun current technical checks."""
    receipt = parse(read_content(root, ref))
    keys(receipt, {"schema_version", "kind", "verdict", "state", "observation"})
    need(type(receipt["schema_version"]) is int and receipt["schema_version"] == 1
         and receipt["kind"] == VERSION and receipt["verdict"] == "technical-pass",
         CODE, "Unsupported or failed process receipt")
    # Byte-canonical comparison also rejects bool/int equality ambiguities.
    need(canonical(receipt["state"]) == canonical(expected_state), CODE, "Process receipt is stale or belongs to another scope")
    obs = parse(read_content(root, receipt["observation"]))
    need("stdout" in obs, CODE, "Process stdout binding is missing")
    output_ref = obs.pop("stdout")
    validate_observation(obs, expected_state)
    validate_success(read_content(root, output_ref), plan_path=expected_state["plan_path"], gate=expected_state["gate"])
    return receipt
