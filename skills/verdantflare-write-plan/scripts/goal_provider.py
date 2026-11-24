"""Read current Codex goal evidence without starting a turn or issuing approval.

The caller supplies the actual workspace/session, never a policy-owned command.
Native files are local evidence, not signed provider attestations.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

from goal_evidence import MAX_CAPTURE_BYTES, _goal
from native_receipts import ReceiptError, _json, identifier

MAX_LOG_BYTES = 512 * 1024 * 1024
MAX_RPC_BYTES = 16 * 1024 * 1024
TIMEOUT_SECONDS = 20


class SourceError(ValueError):
    code = "PL-DELEGATE-SOURCE"


def require(ok: bool, message: str) -> None:
    if not ok:
        raise SourceError(message)


@dataclass(frozen=True)
class SourceReadback:
    session_id: str
    capture: bytes = field(repr=False)
    observation: bytes = field(repr=False)
    executable_sha256: str
    source_bytes: int
    conversation_capture: bytes = field(default=b"", repr=False)


def capture_session(thread: dict, session_id: str, workspace: Path,
                    codex_home: Path, *, include_assistant: bool = False) -> tuple[bytes, tuple[int, int], int]:
    """Select complete original lines from a bounded native-file snapshot."""
    try:
        identifier(session_id)
        require(isinstance(thread, dict) and thread.get("id") == session_id,
                "Provider returned another session")
        require(isinstance(thread.get("cwd"), str)
                and Path(thread["cwd"]).resolve() == workspace.resolve(),
                "Provider workspace differs")
        require(isinstance(thread.get("path"), str) and bool(thread["path"]),
                "Provider did not expose native history")
        path = Path(thread["path"])
        home = codex_home.resolve()
        require(path.is_absolute(), "Native history path is not absolute")
        resolved = path.resolve(strict=True)
        require(any(resolved.is_relative_to(home / folder)
                    for folder in ("sessions", "archived_sessions")),
                "Native history escapes the provider store")
        selected = []; captured = 0
        with resolved.open("rb") as stream:
            stat = os.fstat(stream.fileno())
            require(0 < stat.st_size <= MAX_LOG_BYTES, "Native history size is unsupported")
            remaining = stat.st_size; index = 0
            while remaining:
                raw = stream.readline(min(remaining, MAX_RPC_BYTES) + 1)
                require(0 < len(raw) <= min(remaining, MAX_RPC_BYTES)
                        and raw.endswith(b"\n") and b"\r" not in raw,
                        "Native history is incomplete or oversized")
                remaining -= len(raw)
                row = _json(raw)
                require(isinstance(row, dict) and isinstance(row.get("payload"), dict),
                        "Malformed native history")
                p = row["payload"]; kind = row.get("type")
                if index == 0 or kind == "session_meta":
                    require(kind == "session_meta" and p.get("id") == session_id
                            and p.get("session_id", session_id) == session_id,
                            "Native metadata differs from provider session")
                    require(isinstance(p.get("cwd"), str)
                            and Path(p["cwd"]).resolve() == workspace.resolve(),
                            "Native metadata differs from workspace")
                event = p.get("type")
                # Codex appends refreshed metadata when resuming a session.
                # Validate each identity, retain the original header exactly.
                include = (index == 0
                           or kind == "response_item" and p.get("role") == "user"
                           or include_assistant and kind == "response_item" and p.get("role") == "assistant"
                           or kind == "event_msg" and isinstance(event, str)
                           and (event == "user_message" or event.startswith("thread_goal_")))
                if include:
                    captured += len(raw)
                    require(captured <= MAX_CAPTURE_BYTES, "Selected native history is oversized")
                    selected.append(raw)
                index += 1
            after = os.fstat(stream.fileno())
            require(after.st_size >= stat.st_size, "Native history was truncated")
        require(len(selected) >= 3, "Native user history is missing")
        return b"".join(selected), (stat.st_dev, stat.st_ino), stat.st_size
    except SourceError:
        raise
    except (OSError, RuntimeError, ReceiptError, TypeError, ValueError) as exc:
        raise SourceError("Cannot read complete native evidence") from None


class _ReadOnlyCodex:
    def __init__(self, binary: str):
        self.binary = binary
        self.process = None
        self.sequence = 0

    async def start(self):
        self.process = await asyncio.create_subprocess_exec(
            self.binary, "app-server", "--listen", "stdio://",
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL, limit=MAX_RPC_BYTES,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        await self.call("initialize", {
            "clientInfo": {"name": "vf_plan_goal_provider", "version": "0.1.0"},
            "capabilities": {"experimentalApi": True}})
        self.process.stdin.write(b'{"method":"initialized"}\n')
        await self.process.stdin.drain()

    async def call(self, method: str, params: dict):
        require(method in {"initialize", "thread/read", "thread/goal/get"},
                "Unsupported provider method")
        self.sequence += 1; request_id = self.sequence
        async def exchange():
            self.process.stdin.write((json.dumps({"id": request_id, "method": method,
                                                  "params": params}) + "\n").encode())
            await self.process.stdin.drain()
            for _ in range(128):
                raw = await self.process.stdout.readline()
                require(raw.endswith(b"\n") and len(raw) <= MAX_RPC_BYTES,
                        "Provider returned incomplete output")
                message = _json(raw)
                require(isinstance(message, dict), "Invalid provider response")
                if "method" in message:
                    require("id" not in message, "Provider requested interactive action")
                    continue
                require(type(message.get("id")) is int and message["id"] == request_id,
                        "Provider response does not match request")
                require("error" not in message and "result" in message,
                        "Provider rejected read-only request")
                return message["result"]
            raise SourceError("Provider notification limit exceeded")
        return await asyncio.wait_for(exchange(), TIMEOUT_SECONDS)

    async def close(self):
        if self.process is not None and self.process.returncode is None:
            try:
                self.process.terminate()
                await asyncio.wait_for(self.process.wait(), 5)
            except ProcessLookupError:
                pass
            except asyncio.TimeoutError:
                self.process.kill()
                await self.process.wait()


async def read_codex_goal(session_id: str, workspace: Path, *, include_decisions: bool = False) -> SourceReadback:
    """Fresh read-only provider call. No fallback to captured observation files."""
    client = None
    try:
        identifier(session_id)
        binary = shutil.which("codex")
        require(bool(binary), "Installed Codex executable is unavailable")
        executable = Path(binary).resolve(strict=True)
        binary_hash = hashlib.sha256(executable.read_bytes()).hexdigest()
        home = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))
        client = _ReadOnlyCodex(str(executable))
        await client.start()
        result = await client.call("thread/read", {"threadId": session_id, "includeTurns": False})
        require(isinstance(result, dict), "Invalid thread response")
        thread = result.get("thread")
        first, identity, size = capture_session(thread, session_id, workspace, home, include_assistant=include_decisions)
        observation = await client.call("thread/goal/get", {"threadId": session_id})
        require(isinstance(observation, dict), "Missing goal readback")
        _goal(observation.get("goal"), session_id)
        second, current_identity, current_size = capture_session(thread, session_id, workspace, home, include_assistant=include_decisions)
        require(identity == current_identity and current_size >= size and first == second,
                "Native user history changed during readback")
        require(hashlib.sha256(executable.read_bytes()).hexdigest() == binary_hash,
                "Provider executable changed during readback")
        raw = (json.dumps(observation, ensure_ascii=False, separators=(",", ":")) + "\n").encode()
        user_capture = b"".join(line for line in second.splitlines(keepends=True)
                               if not (_json(line).get("type") == "response_item"
                                       and _json(line).get("payload", {}).get("role") == "assistant"))
        return SourceReadback(session_id, user_capture, raw, binary_hash, current_size,
                              second if include_decisions else b"")
    except SourceError:
        raise
    except (OSError, RuntimeError, asyncio.TimeoutError, ValueError, ReceiptError):
        raise SourceError("Native goal provider is unavailable or returned invalid evidence") from None
    finally:
        if client is not None:
            await client.close()
