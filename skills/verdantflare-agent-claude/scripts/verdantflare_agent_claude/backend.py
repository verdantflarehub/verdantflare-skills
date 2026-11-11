"""Claude Code panel pipe (uds-messaging) and managed stream-json CLI backend."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path
import re
import time
import uuid

from .errors import BridgeError


def claude_project_dir_name(cwd: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]", "-", cwd)


def _pid_alive(pid: int) -> bool:
    if not isinstance(pid, int) or pid <= 0:
        return False
    if os.name == "nt":
        import ctypes
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        try:
            exit_code = ctypes.c_ulong()
            if kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                return exit_code.value == STILL_ACTIVE
            return True
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def read_peer_token(sessions_dir: Path, pid: int, pipe_path: str) -> str | None:
    if not pipe_path:
        return None
    digest = hashlib.sha256(pipe_path.lower().encode("utf-8")).hexdigest()
    key_file = sessions_dir / f"{pid}.{digest}.key"
    if not key_file.exists():
        return None
    try:
        data = json.loads(key_file.read_text(encoding="utf-8"))
        token = data.get("peerToken")
        if isinstance(token, str) and len(token) == 32:
            return token
    except Exception:
        return None
    return None


def find_live_panel_sessions(claude_dir: Path) -> dict[str, dict]:
    sessions_dir = claude_dir / "sessions"
    if not sessions_dir.exists():
        return {}
    live: dict[str, dict] = {}
    for jf in sorted(sessions_dir.glob("*.json")):
        try:
            meta = json.loads(jf.read_text(encoding="utf-8"))
        except Exception:
            continue
        sid = meta.get("sessionId")
        pid = meta.get("pid")
        pipe = meta.get("messagingSocketPath") or ""
        if not isinstance(sid, str) or not sid or not _pid_alive(pid):
            continue
        token = read_peer_token(sessions_dir, pid, pipe)
        pipe_ok = bool(pipe and os.path.exists(pipe) and token)
        live[sid] = {
            "session_id": sid,
            "pid": pid,
            "cwd": meta.get("cwd") or "",
            "name": meta.get("name"),
            "status": meta.get("status") or "unknown",
            "entrypoint": meta.get("entrypoint") or "",
            "pipe_path": pipe,
            "peer_token": token,
            "panel_reachable": pipe_ok,
            "updated_at": meta.get("updatedAt") or meta.get("startedAt") or 0,
            "meta_path": str(jf),
        }
    return live


def _extract_transcript_summary(jsonl_path: Path) -> dict | None:
    try:
        stat = jsonl_path.stat()
    except OSError:
        return None
    sid = jsonl_path.stem
    cwd = None
    title = None
    first_user = None
    try:
        with jsonl_path.open("r", encoding="utf-8", errors="replace") as fh:
            for idx, line in enumerate(fh):
                if idx > 120 and cwd and (title or first_user):
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                if not isinstance(rec, dict):
                    continue
                if not cwd and isinstance(rec.get("cwd"), str):
                    cwd = rec["cwd"]
                rtype = rec.get("type")
                if rtype in ("ai-title", "custom-title") and isinstance(rec.get("title"), str):
                    title = rec["title"].strip()
                elif rtype == "user" and not first_user:
                    msg = rec.get("message")
                    if isinstance(msg, dict):
                        content = msg.get("content")
                        if isinstance(content, str) and content.strip():
                            first_user = content.strip()[:80]
                        elif isinstance(content, list):
                            for blk in content:
                                if isinstance(blk, dict) and blk.get("type") == "text" and isinstance(blk.get("text"), str):
                                    first_user = blk["text"].strip()[:80]
                                    break
    except OSError:
        return None
    return {
        "session_id": sid,
        "cwd": cwd,
        "title": title or first_user or sid,
        "updated_at": int(stat.st_mtime * 1000),
        "transcript_path": str(jsonl_path),
    }


def list_claude_sessions(claude_dir: Path, target_cwd: str | None = None) -> list[dict]:
    live_map = find_live_panel_sessions(claude_dir)
    projects_dir = claude_dir / "projects"
    merged: dict[str, dict] = {}

    if projects_dir.exists():
        buckets = [d for d in projects_dir.iterdir() if d.is_dir()]
        if target_cwd:
            expected_bucket = claude_project_dir_name(target_cwd).lower()
            buckets = [b for b in buckets if b.name.lower() == expected_bucket]
        for bucket in buckets:
            for jf in sorted(bucket.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)[:40]:
                info = _extract_transcript_summary(jf)
                if not info:
                    continue
                sid = info["session_id"]
                live = live_map.get(sid)
                cwd_val = (live and live.get("cwd")) or info.get("cwd") or target_cwd or ""
                if not cwd_val:
                    continue
                merged[sid] = {
                    "session_id": sid,
                    "thread_id": sid,
                    "title": (live and live.get("name")) or info["title"],
                    "cwd": cwd_val,
                    "updated_at": max(info["updated_at"], int(live["updated_at"])) if live else info["updated_at"],
                    "panel_reachable": bool(live and live["panel_reachable"]),
                    "panel_status": live["status"] if live else "offline",
                    "panel_pid": live["pid"] if live else None,
                }

    for sid, live in live_map.items():
        if sid not in merged and live.get("cwd"):
            if target_cwd and os.path.normcase(live["cwd"]) != os.path.normcase(target_cwd):
                continue
            merged[sid] = {
                "session_id": sid,
                "thread_id": sid,
                "title": live.get("name") or sid,
                "cwd": live["cwd"],
                "updated_at": int(live["updated_at"]),
                "panel_reachable": bool(live["panel_reachable"]),
                "panel_status": live["status"],
                "panel_pid": live["pid"],
            }

    return sorted(merged.values(), key=lambda x: x["updated_at"], reverse=True)


def locate_transcript_path(claude_dir: Path, cwd: str, session_id: str) -> Path:
    projects_dir = claude_dir / "projects"
    target_bucket = claude_project_dir_name(cwd)
    if projects_dir.exists():
        for b in projects_dir.iterdir():
            if b.is_dir() and b.name.lower() == target_bucket.lower():
                return b / f"{session_id}.jsonl"
    return projects_dir / target_bucket / f"{session_id}.jsonl"


def send_pipe_message(pipe_path: str, peer_token: str, session_id: str, turn_uuid: str, prompt: str) -> None:
    auth_frame = json.dumps({"type": "auth", "token": peer_token}, ensure_ascii=False)
    user_frame = json.dumps(
        {
            "type": "user",
            "session_id": session_id,
            "uuid": turn_uuid,
            "priority": "next",
            "message": {"role": "user", "content": prompt},
        },
        ensure_ascii=False,
    )
    payload = (auth_frame + "\n" + user_frame + "\n").encode("utf-8")
    try:
        with open(pipe_path, "r+b", buffering=0) as pipe:
            pipe.write(payload)
    except OSError:
        try:
            with open(pipe_path, "wb", buffering=0) as pipe:
                pipe.write(payload)
        except OSError as exc:
            raise BridgeError("BACKEND_DISCONNECTED", f"Failed to write to Claude panel pipe: {exc}") from None


def _parse_assistant_content(msg: dict, commands: list[dict], file_changes: list[str], texts: list[str]) -> list[tuple[str, dict]]:
    events: list[tuple[str, dict]] = []
    content = msg.get("content")
    if isinstance(content, str) and content.strip():
        texts.append(content)
        events.append(("assistant_text", {"text": content}))
    elif isinstance(content, list):
        for blk in content:
            if not isinstance(blk, dict):
                continue
            btype = blk.get("type")
            if btype == "text" and isinstance(blk.get("text"), str):
                texts.append(blk["text"])
                events.append(("assistant_text", {"text": blk["text"]}))
            elif btype == "thinking" and isinstance(blk.get("thinking"), str):
                events.append(("thinking", {"text": blk["thinking"][:500]}))
            elif btype == "tool_use":
                tname = blk.get("name") or "tool"
                tinput = blk.get("input") if isinstance(blk.get("input"), dict) else {}
                events.append(("tool_use", {"id": blk.get("id"), "name": tname}))
                if tname in ("Bash", "PowerShell") and isinstance(tinput.get("command"), str):
                    commands.append({"id": blk.get("id"), "command": tinput["command"], "exit_code": None})
                elif tname in ("Edit", "Write", "MultiEdit") and isinstance(tinput.get("file_path"), str):
                    if tinput["file_path"] not in file_changes:
                        file_changes.append(tinput["file_path"])
    return events


async def run_panel_turn(
    *,
    claude_dir: Path,
    cwd: str,
    session_id: str,
    turn_id: str,
    prompt: str,
    deadline: float,
    on_event,
    is_cancelling,
) -> dict:
    live_map = find_live_panel_sessions(claude_dir)
    live = live_map.get(session_id)
    if not live or not live["panel_reachable"]:
        raise BridgeError("OWNER_UNAVAILABLE", f"VS Code Claude Code panel session {session_id} is not reachable")
    if live["status"] != "idle":
        raise BridgeError("SESSION_BUSY", f"VS Code Claude Code panel session {session_id} is currently {live['status']}", retryable=True)

    transcript = locate_transcript_path(claude_dir, live["cwd"] or cwd, session_id)
    start_offset = transcript.stat().st_size if transcript.exists() else 0
    initial_updated_at = int(live.get("updated_at") or 0)

    await asyncio.to_thread(
        send_pipe_message,
        live["pipe_path"],
        live["peer_token"],
        session_id,
        turn_id,
        prompt,
    )
    injected_at = time.time()
    ack_deadline = min(deadline, injected_at + 30.0)
    on_event("panel_injected", {"session_id": session_id, "turn_id": turn_id, "pid": live["pid"]})

    commands: list[dict] = []
    file_changes: list[str] = []
    texts: list[str] = []
    user_seen = False
    assistant_end_turn = False
    meta_file = Path(live["meta_path"])

    while time.time() < deadline:
        if is_cancelling():
            raise BridgeError("APPROVAL_REQUIRES_UI", "Panel turn cancellation must be triggered in the VS Code Claude Code UI")
        if transcript.exists():
            try:
                with transcript.open("rb") as fh:
                    fh.seek(start_offset)
                    chunk = fh.read()
                    if chunk:
                        lines = chunk.splitlines(keepends=True)
                        consumed = 0
                        for ln in lines:
                            if not ln.endswith(b"\n") and not ln.endswith(b"\r"):
                                break
                            consumed += len(ln)
                            raw = ln.decode("utf-8", errors="replace").strip()
                            if not raw:
                                continue
                            try:
                                rec = json.loads(raw)
                            except Exception:
                                continue
                            rtype = rec.get("type")
                            if rtype == "user":
                                user_seen = True
                            elif rtype == "assistant" and isinstance(rec.get("message"), dict):
                                user_seen = True
                                msg = rec["message"]
                                for ekind, epayload in _parse_assistant_content(msg, commands, file_changes, texts):
                                    on_event(ekind, epayload)
                                if msg.get("stop_reason") == "end_turn":
                                    assistant_end_turn = True
                            elif rtype in ("last-prompt", "cost-state") and assistant_end_turn:
                                return {
                                    "answer": "\n".join(t for t in texts if t.strip()).strip(),
                                    "commands": commands,
                                    "file_changes": file_changes,
                                    "unresolved": [],
                                }
                        start_offset += consumed
            except OSError:
                pass

        if meta_file.exists():
            try:
                cur_meta = json.loads(meta_file.read_text(encoding="utf-8"))
                cur_status = cur_meta.get("status")
                cur_updated = int(cur_meta.get("updatedAt") or 0)
                if cur_status not in ("idle", "unknown", None) or cur_updated > initial_updated_at:
                    user_seen = True
                if assistant_end_turn and user_seen and cur_status == "idle":
                    return {
                        "answer": "\n".join(t for t in texts if t.strip()).strip(),
                        "commands": commands,
                        "file_changes": file_changes,
                        "unresolved": [],
                    }
            except Exception:
                pass

        if not user_seen and time.time() >= ack_deadline:
            raise BridgeError(
                "BACKEND_DISCONNECTED",
                f"VS Code Claude Code panel session {session_id} did not acknowledge injected user message within 30s",
            )

        await asyncio.sleep(0.25)

    raise BridgeError("BACKEND_TIMEOUT", "Timed out waiting for VS Code Claude Code panel turn completion")


class ManagedTurnHandle:
    def __init__(self, process: asyncio.subprocess.Process):
        self.process = process

    async def cancel(self) -> None:
        if self.process.returncode is None:
            self.process.terminate()
            try:
                await asyncio.wait_for(self.process.wait(), timeout=5)
            except TimeoutError:
                self.process.kill()
                await self.process.wait()


async def run_managed_turn(
    *,
    claude_binary: str,
    claude_dir: Path,
    cwd: str,
    session_id: str,
    is_resume: bool,
    prompt: str,
    deadline: float,
    on_event,
    register_handle,
    is_cancelling,
) -> dict:
    cmd = [
        claude_binary,
        "-p",
        "--output-format",
        "stream-json",
        "--verbose",
        "--permission-mode",
        "bypassPermissions",
    ]
    transcript = locate_transcript_path(claude_dir, cwd, session_id)
    if is_resume or transcript.exists():
        cmd.extend(["--resume", session_id])
    else:
        cmd.extend(["--session-id", session_id])

    env = dict(os.environ)
    env.pop("CLAUDECODE", None)
    env["VERDANTFLARE_AGENT_CLAUDE_DEPTH"] = "1"

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=cwd,
        env=env,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        limit=16 * 1024 * 1024,
    )
    register_handle(ManagedTurnHandle(proc))

    if proc.stdin:
        try:
            proc.stdin.write(prompt.encode("utf-8"))
            await proc.stdin.drain()
        except Exception:
            pass
        finally:
            proc.stdin.close()

    stderr_chunks: list[bytes] = []

    async def _drain_stderr() -> None:
        if not proc.stderr:
            return
        try:
            while True:
                chunk = await proc.stderr.read(4096)
                if not chunk:
                    break
                if sum(len(c) for c in stderr_chunks) < 65536:
                    stderr_chunks.append(chunk)
        except Exception:
            pass

    stderr_task = asyncio.create_task(_drain_stderr())

    commands: list[dict] = []
    file_changes: list[str] = []
    texts: list[str] = []
    final_result_text: str | None = None
    is_error = False
    error_detail: str | None = None

    try:
        while True:
            remaining = deadline - time.time()
            if remaining <= 0:
                await ManagedTurnHandle(proc).cancel()
                raise BridgeError("BACKEND_TIMEOUT", "Managed Claude CLI turn timed out")
            if is_cancelling():
                await ManagedTurnHandle(proc).cancel()
                break
            try:
                line = await asyncio.wait_for(proc.stdout.readline(), timeout=min(remaining, 2.0))
            except TimeoutError:
                continue
            if not line:
                break
            raw = line.decode("utf-8", errors="replace").strip()
            if not raw:
                continue
            try:
                event = json.loads(raw)
            except Exception:
                continue
            if not isinstance(event, dict):
                continue
            etype = event.get("type")
            if etype == "assistant" and isinstance(event.get("message"), dict):
                for ekind, epayload in _parse_assistant_content(event["message"], commands, file_changes, texts):
                    on_event(ekind, epayload)
            elif etype == "result":
                if isinstance(event.get("result"), str):
                    final_result_text = event["result"]
                if event.get("is_error") is True:
                    is_error = True
                    error_detail = str(event.get("result") or event.get("subtype") or "Claude CLI reported error")
                on_event("result", {"subtype": event.get("subtype"), "is_error": bool(event.get("is_error"))})

        await asyncio.wait_for(proc.wait(), timeout=10)
    except TimeoutError:
        await ManagedTurnHandle(proc).cancel()
        raise BridgeError("BACKEND_TIMEOUT", "Managed Claude CLI turn timed out") from None
    finally:
        try:
            await asyncio.wait_for(stderr_task, timeout=2.0)
        except Exception:
            stderr_task.cancel()

    if is_cancelling():
        return {
            "cancelled": True,
            "answer": final_result_text or "\n".join(t for t in texts if t.strip()).strip(),
            "commands": commands,
            "file_changes": file_changes,
            "unresolved": [],
        }

    if is_error or (proc.returncode not in (0, None)):
        stderr_txt = b"".join(stderr_chunks).decode("utf-8", errors="replace").strip()[:400]
        raise BridgeError("BACKEND_DISCONNECTED", error_detail or stderr_txt or f"Claude CLI exited with {proc.returncode}")

    answer = final_result_text if final_result_text is not None else "\n".join(t for t in texts if t.strip()).strip()
    return {
        "answer": answer,
        "commands": commands,
        "file_changes": file_changes,
        "unresolved": [],
    }


_VERSION_CACHE: dict[str, str] = {}


def _latest_claude_extension() -> Path | None:
    ext_root = Path.home() / ".vscode" / "extensions"
    if not ext_root.is_dir():
        return None
    obsolete: dict = {}
    obs_file = ext_root / ".obsolete"
    if obs_file.is_file():
        try:
            loaded = json.loads(obs_file.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                obsolete = loaded
        except Exception:
            obsolete = {}
    dirs = [d for d in ext_root.glob("anthropic.claude-code-*") if d.is_dir() and (d / "package.json").is_file()]
    active = [d for d in dirs if not obsolete.get(d.name)] or dirs
    if not active:
        return None
    return max(active, key=lambda p: p.stat().st_mtime).resolve()


def resolve_claude_binary(configured: str | None = None, extension_dir: str | None = None) -> str:
    cfg_str = str(configured or "")
    if cfg_str and ".vscode" not in cfg_str.lower():
        if Path(cfg_str).is_file() or not os.path.isabs(cfg_str):
            return cfg_str
    exe_name = "claude.exe" if os.name == "nt" else "claude"
    latest_ext = _latest_claude_extension()
    if latest_ext is not None:
        cand = latest_ext / "resources" / "native-binary" / exe_name
        if cand.is_file():
            return str(cand.resolve())
    if extension_dir:
        cand = Path(extension_dir) / "resources" / "native-binary" / exe_name
        if cand.is_file():
            return str(cand.resolve())
    if cfg_str and Path(cfg_str).is_file():
        return cfg_str
    import shutil
    which_bin = shutil.which("claude")
    return str(Path(which_bin).resolve()) if which_bin else cfg_str


def detect_claude_versions(claude_binary: str, extension_dir: str | None = None) -> tuple[str, str]:
    resolved_bin = resolve_claude_binary(claude_binary, extension_dir)
    ext_ver = "unknown"
    candidates: list[Path] = []
    latest_ext = _latest_claude_extension()
    if latest_ext is not None:
        candidates.append(latest_ext / "package.json")
    if extension_dir:
        candidates.append(Path(extension_dir) / "package.json")
    if resolved_bin:
        bin_path = Path(resolved_bin)
        for parent in bin_path.parents:
            pkg = parent / "package.json"
            if pkg.exists():
                candidates.append(pkg)
                break
    for pkg in candidates:
        if pkg.exists():
            try:
                data = json.loads(pkg.read_text(encoding="utf-8"))
                if isinstance(data.get("version"), str) and data["version"].strip():
                    ext_ver = data["version"].strip()
                    break
            except Exception:
                continue

    cache_key = f"cli:{resolved_bin}"
    cli_ver = _VERSION_CACHE.get(cache_key)
    if not cli_ver:
        cli_ver = "unavailable"
        if resolved_bin and Path(resolved_bin).exists():
            import subprocess
            try:
                proc = subprocess.run(
                    [resolved_bin, "--version"],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=3.0,
                )
                out = (proc.stdout or proc.stderr or "").strip()
                if out:
                    m = re.search(r"(\d+\.\d+\.\d+[^\s]*)", out)
                    cli_ver = m.group(1) if m else out.splitlines()[0][:40]
                else:
                    cli_ver = ext_ver if ext_ver != "unknown" else "unknown"
            except Exception:
                cli_ver = ext_ver if ext_ver != "unknown" else "unknown"
        _VERSION_CACHE[cache_key] = cli_ver

    return ext_ver, cli_ver
