"""Grok CLI ACP stdio backend and AFK Pilot session-meta synchronization."""
from __future__ import annotations

import asyncio
from datetime import datetime
import json
import os
from pathlib import Path
import re
import tempfile
import time
import urllib.parse
import uuid

from .errors import BridgeError

MEDIA_PATH_RE = re.compile(r"([A-Za-z]:[\\/][^\s\"'<>|]+?\.(?:png|jpg|jpeg|webp|mp4|webm))", re.IGNORECASE)


def _parse_iso_ms(ts: str | None) -> int:
    if not ts or not isinstance(ts, str):
        return 0
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return int(dt.timestamp() * 1000)
    except Exception:
        return 0


def sync_afk_pilot_session_meta(grok_home: Path, session_id: str, cwd: str, title: str | None = None) -> dict:
    """Register or refresh session metadata in ~/.grok/client-state/session-meta.json for AFK Pilot UI."""
    meta_file = grok_home / "client-state" / "session-meta.json"
    meta_file.parent.mkdir(parents=True, exist_ok=True)
    data: dict = {}
    if meta_file.exists():
        try:
            loaded = json.loads(meta_file.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                data = loaded
        except Exception:
            data = {}

    existing = data.get(session_id) if isinstance(data.get(session_id), dict) else {}
    now_ms = int(time.time() * 1000)
    clean_title = (existing.get("autoName") or title or session_id).strip()
    clean_title = re.sub(r"\s+", " ", clean_title)[:60]
    entry = {
        **existing,
        "provider": "grok",
        "providerCwd": cwd,
        "activeAt": now_ms,
        "autoName": clean_title,
    }
    data[session_id] = entry
    tmp_file = meta_file.with_name(f".session-meta.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp")
    try:
        tmp_file.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        last_err: OSError | None = None
        for _ in range(5):
            try:
                tmp_file.replace(meta_file)
                last_err = None
                break
            except OSError as exc:
                last_err = exc
                time.sleep(0.05)
        if last_err is not None:
            raise last_err
    finally:
        if tmp_file.exists():
            try:
                tmp_file.unlink()
            except OSError:
                pass
    return entry


def list_grok_sessions(grok_home: Path, target_cwd: str | None = None) -> list[dict]:
    sessions_root = grok_home / "sessions"
    meta_file = grok_home / "client-state" / "session-meta.json"
    client_meta: dict = {}
    if meta_file.exists():
        try:
            loaded = json.loads(meta_file.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                client_meta = loaded
        except Exception:
            client_meta = {}

    merged: dict[str, dict] = {}
    if sessions_root.exists():
        for bucket in sessions_root.iterdir():
            if not bucket.is_dir():
                continue
            decoded_cwd = urllib.parse.unquote(bucket.name)
            if target_cwd and os.path.normcase(decoded_cwd) != os.path.normcase(target_cwd):
                continue
            for sdir in bucket.iterdir():
                if not sdir.is_dir():
                    continue
                summary_path = sdir / "summary.json"
                if not summary_path.exists():
                    continue
                try:
                    summary = json.loads(summary_path.read_text(encoding="utf-8"))
                except Exception:
                    continue
                if not isinstance(summary, dict):
                    continue
                sid = summary.get("id") or sdir.name
                cwd_val = summary.get("working_dir") or decoded_cwd
                if not cwd_val:
                    continue
                if target_cwd and os.path.normcase(cwd_val) != os.path.normcase(target_cwd):
                    continue
                cmeta = client_meta.get(sid) if isinstance(client_meta.get(sid), dict) else {}
                updated_ms = max(
                    _parse_iso_ms(summary.get("updated_at")),
                    int(cmeta.get("activeAt") or 0),
                    int(summary_path.stat().st_mtime * 1000),
                )
                skind = summary.get("session_kind")
                title = (
                    cmeta.get("customName")
                    or cmeta.get("autoName")
                    or summary.get("title")
                    or sid
                )
                merged[sid] = {
                    "session_id": sid,
                    "thread_id": sid,
                    "title": str(title).strip()[:80],
                    "cwd": cwd_val,
                    "updated_at": updated_ms,
                    "session_kind": skind,
                    "afk_pilot_visible": skind != "headless",
                    "afk_pilot_registered": bool(cmeta and cmeta.get("provider") == "grok"),
                }

    return sorted(merged.values(), key=lambda x: x["updated_at"], reverse=True)


class AcpTurnHandle:
    def __init__(self, process: asyncio.subprocess.Process, session_id_getter, *, is_acp: bool = True):
        self.process = process
        self._get_session_id = session_id_getter
        self.is_acp = is_acp
        self.cancel_sent = False

    async def request_cancel(self) -> bool:
        sid = self._get_session_id()
        if sid and self.process.stdin and not self.process.stdin.is_closing() and not self.cancel_sent:
            self.cancel_sent = True
            try:
                frame = json.dumps(
                    {"jsonrpc": "2.0", "method": "session/cancel", "params": {"sessionId": sid}},
                    ensure_ascii=False,
                )
                self.process.stdin.write((frame + "\n").encode("utf-8"))
                await self.process.stdin.drain()
                return True
            except Exception:
                return False
        return self.cancel_sent

    async def force_stop(self) -> None:
        if self.process.returncode is None:
            self.process.terminate()
            try:
                await asyncio.wait_for(self.process.wait(), timeout=3.0)
            except TimeoutError:
                self.process.kill()
                await self.process.wait()

    async def cancel(self) -> None:
        if self.is_acp and await self.request_cancel():
            return
        await self.force_stop()


def _extract_update_event(
    update: dict,
    texts: list[str],
    commands: list[dict],
    file_changes: list[str],
    media_paths: list[str],
) -> tuple[str, dict] | None:
    utype = update.get("sessionUpdate") or update.get("type") or ""
    if utype in ("agent_message_chunk", "messageChunk"):
        content = update.get("content")
        text_val = ""
        if isinstance(content, dict) and isinstance(content.get("text"), str):
            text_val = content["text"]
        elif isinstance(update.get("text"), str):
            text_val = update["text"]
        if text_val:
            texts.append(text_val)
            for m in MEDIA_PATH_RE.findall(text_val):
                if m not in media_paths:
                    media_paths.append(m)
            return ("assistant_text", {"text": text_val})
    elif utype in ("agent_thought_chunk", "thoughtChunk"):
        content = update.get("content")
        thought_val = ""
        if isinstance(content, dict) and isinstance(content.get("text"), str):
            thought_val = content["text"]
        elif isinstance(update.get("text"), str):
            thought_val = update["text"]
        if thought_val:
            return ("thinking", {"text": thought_val[:500]})
    elif utype in ("tool_call", "toolCall", "tool_call_update", "toolCallUpdate"):
        tc_id = update.get("toolCallId") or update.get("id") or "tool"
        title = update.get("title") or update.get("name") or utype
        status = update.get("status")
        metadata = (update.get('_meta') or {}).get('x.ai/tool') or {}
        read_only = (update.get('kind') in {'read', 'search'} or metadata.get('read_only') is True
                     or metadata.get('kind') in {'read', 'search'})
        raw_input = update.get("rawInput") or update.get("input") or {}
        if isinstance(raw_input, dict):
            cmd = raw_input.get("command") or raw_input.get("cmd")
            if isinstance(cmd, str) and not any(c.get("id") == tc_id for c in commands):
                commands.append({"id": tc_id, "command": cmd, "status": status})
            fpath = raw_input.get("file_path") or raw_input.get("path")
            if not read_only and isinstance(fpath, str) and fpath not in file_changes:
                file_changes.append(fpath)
        locations = update.get("locations")
        if not read_only and isinstance(locations, list):
            for loc in locations:
                if isinstance(loc, dict) and isinstance(loc.get("path"), str):
                    if loc["path"] not in file_changes:
                        file_changes.append(loc["path"])
        return ("tool_call", {"id": tc_id, "title": title, "status": status})
    return None


async def run_acp_turn(
    *,
    grok_binary: str,
    grok_home: Path,
    cwd: str,
    session_id: str | None,
    is_resume: bool,
    prompt: str,
    title_hint: str,
    deadline: float,
    on_event,
    on_session_bound,
    register_handle,
    is_cancelling,
) -> dict:
    env = dict(os.environ)
    env["VERDANTFLARE_AGENT_GROK_DEPTH"] = "1"

    proc = await asyncio.create_subprocess_exec(
        grok_binary,
        "agent",
        "--always-approve",
        "--no-leader",
        "stdio",
        cwd=cwd,
        env=env,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        limit=16 * 1024 * 1024,
    )

    active_session_id = session_id
    handle = AcpTurnHandle(proc, lambda: active_session_id, is_acp=True)
    register_handle(handle)

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

    async def send_rpc(msg: dict) -> None:
        if not proc.stdin or proc.stdin.is_closing():
            raise BridgeError("BACKEND_DISCONNECTED", "ACP stdin is closed")
        line = json.dumps(msg, ensure_ascii=False) + "\n"
        proc.stdin.write(line.encode("utf-8"))
        await proc.stdin.drain()

    async def read_until_id(target_id: int, *, collect_updates: bool = False) -> dict:
        cancel_deadline: float | None = None
        while True:
            remaining = deadline - time.time()
            if remaining <= 0:
                await handle.force_stop()
                raise BridgeError("BACKEND_TIMEOUT", "Grok ACP turn timed out")
            if is_cancelling() and collect_updates:
                if not handle.cancel_sent:
                    await handle.request_cancel()
                if cancel_deadline is None:
                    cancel_deadline = time.time() + 5.0
                elif time.time() >= cancel_deadline:
                    await handle.force_stop()
                    return {"stopReason": "cancelled"}
            wait_slice = 0.4 if (cancel_deadline is not None) else 2.0
            try:
                raw_line = await asyncio.wait_for(proc.stdout.readline(), timeout=min(remaining, wait_slice))
            except TimeoutError:
                continue
            if not raw_line:
                if is_cancelling() and collect_updates:
                    return {"stopReason": "cancelled"}
                stderr_txt = b"".join(stderr_chunks).decode("utf-8", errors="replace").strip()[:400]
                raise BridgeError("BACKEND_DISCONNECTED", stderr_txt or "Grok ACP stdio closed unexpectedly")
            txt = raw_line.decode("utf-8", errors="replace").strip()
            if not txt:
                continue
            try:
                msg = json.loads(txt)
            except Exception:
                continue
            if not isinstance(msg, dict):
                continue

            if msg.get("method") == "session/update" and isinstance(msg.get("params"), dict):
                if collect_updates:
                    if msg['params'].get('sessionId') != active_session_id:
                        raise BridgeError('BACKEND_TARGET_MISMATCH', 'ACP update belongs to another session')
                    upd = msg["params"].get("update")
                    if isinstance(upd, dict):
                        parsed = _extract_update_event(upd, texts, commands, file_changes, media_paths)
                        if parsed:
                            on_event(parsed[0], parsed[1])
                continue

            if "id" in msg and "method" in msg and "result" not in msg and "error" not in msg:
                if msg.get("method") == "session/request_permission":
                    params = msg.get("params") if isinstance(msg.get("params"), dict) else {}
                    options = params.get("options") if isinstance(params.get("options"), list) else []
                    chosen_option = "allow"
                    for opt in options:
                        if isinstance(opt, dict):
                            oid = str(opt.get("optionId") or opt.get("id") or "")
                            kind = str(opt.get("kind") or "")
                            if kind in ("allow_once", "allow_always", "allow") or "allow" in oid.lower():
                                chosen_option = oid or chosen_option
                                break
                    else:
                        if options and isinstance(options[0], dict) and options[0].get("optionId"):
                            chosen_option = str(options[0]["optionId"])
                    await send_rpc(
                        {
                            "jsonrpc": "2.0",
                            "id": msg["id"],
                            "result": {"outcome": {"outcome": "selected", "optionId": chosen_option}},
                        }
                    )
                    on_event("permission_auto_approved", {"option_id": chosen_option})
                    continue

                await send_rpc(
                    {
                        "jsonrpc": "2.0",
                        "id": msg["id"],
                        "error": {"code": -32601, "message": "Client capability disabled; use built-in agent tools"},
                    }
                )
                continue

            if msg.get("id") == target_id:
                if "error" in msg and msg["error"]:
                    if is_cancelling() and collect_updates:
                        return {"stopReason": "cancelled"}
                    err = msg["error"]
                    err_msg = err.get("message") if isinstance(err, dict) else str(err)
                    raise BridgeError("BACKEND_DISCONNECTED", f"Grok ACP error on id={target_id}: {err_msg}")
                return msg.get("result") if isinstance(msg.get("result"), dict) else {}

    texts: list[str] = []
    commands: list[dict] = []
    file_changes: list[str] = []
    media_paths: list[str] = []
    afk_synced = False

    try:
        await send_rpc(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": 1,
                    "clientInfo": {"name": "verdantflare-agent-grok", "version": "0.1.0"},
                    "clientCapabilities": {
                        "fs": {"readTextFile": False, "writeTextFile": False},
                        "terminal": False,
                    },
                },
            }
        )
        await read_until_id(1)

        if is_resume and active_session_id:
            await send_rpc(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "session/load",
                    "params": {"sessionId": active_session_id, "cwd": cwd, "mcpServers": []},
                }
            )
            load_res = await read_until_id(2)
            if load_res.get('sessionId') not in (None, active_session_id):
                raise BridgeError('BACKEND_TARGET_MISMATCH', 'ACP load returned another session')
            active_session_id = load_res.get("sessionId") or active_session_id
        else:
            await send_rpc(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "session/new",
                    "params": {"cwd": cwd, "mcpServers": []},
                }
            )
            new_res = await read_until_id(2)
            active_session_id = new_res.get("sessionId") or active_session_id

        if not active_session_id:
            raise BridgeError("BACKEND_DISCONNECTED", "Grok ACP did not return a sessionId")

        on_session_bound(active_session_id)
        try:
            sync_afk_pilot_session_meta(grok_home, active_session_id, cwd, title_hint)
            afk_synced = True
            on_event("afk_pilot_synced", {"session_id": active_session_id, "cwd": cwd})
        except Exception as exc:
            on_event("afk_pilot_sync_warning", {"session_id": active_session_id, "error": str(exc)})

        await send_rpc(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "session/prompt",
                "params": {
                    "sessionId": active_session_id,
                    "prompt": [{"type": "text", "text": prompt}],
                },
            }
        )
        prompt_res = await read_until_id(3, collect_updates=True)
        stop_reason = prompt_res.get("stopReason") or "end_turn"
        try:
            sync_afk_pilot_session_meta(grok_home, active_session_id, cwd, title_hint)
            afk_synced = True
        except Exception as exc:
            on_event("afk_pilot_sync_warning", {"session_id": active_session_id, "error": str(exc)})
    finally:
        if proc.stdin and not proc.stdin.is_closing():
            proc.stdin.close()
        await handle.force_stop()
        try:
            await asyncio.wait_for(stderr_task, timeout=2.0)
        except Exception:
            stderr_task.cancel()

    cancelled = is_cancelling() or stop_reason == "cancelled"
    return {
        "cancelled": cancelled,
        "session_id": active_session_id,
        "stop_reason": stop_reason,
        "answer": "".join(texts).strip(),
        "commands": commands,
        "file_changes": file_changes,
        "media": media_paths,
        "afk_pilot_synced": afk_synced,
        "unresolved": [],
    }


async def run_headless_turn(
    *,
    grok_binary: str,
    cwd: str,
    session_id: str | None,
    prompt: str,
    deadline: float,
    register_handle,
    is_cancelling,
) -> dict:
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", suffix=".prompt.txt", delete=False) as tf:
        tf.write(prompt)
        prompt_file_path = Path(tf.name)

    cmd = [grok_binary, "--output-format", "json", "--prompt-file", str(prompt_file_path)]
    if session_id:
        cmd.extend(["--resume", session_id])

    env = dict(os.environ)
    env["VERDANTFLARE_AGENT_GROK_DEPTH"] = "1"
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=cwd,
        env=env,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        limit=16 * 1024 * 1024,
    )
    handle = AcpTurnHandle(proc, lambda: session_id, is_acp=False)
    register_handle(handle)
    remaining = max(1.0, deadline - time.time())
    try:
        stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=remaining)
    except TimeoutError:
        await handle.force_stop()
        raise BridgeError("BACKEND_TIMEOUT", "Headless Grok CLI turn timed out") from None
    finally:
        if prompt_file_path.exists():
            try:
                prompt_file_path.unlink()
            except OSError:
                pass

    if is_cancelling():
        return {
            "cancelled": True,
            "session_id": session_id,
            "answer": "",
            "commands": [],
            "file_changes": [],
            "media": [],
            "afk_pilot_synced": False,
            "unresolved": [],
        }

    if proc.returncode not in (0, None):
        err_txt = stderr_b.decode("utf-8", errors="replace").strip()[:400]
        raise BridgeError("BACKEND_DISCONNECTED", err_txt or f"Grok CLI exited with {proc.returncode}")

    out_txt = stdout_b.decode("utf-8", errors="replace").strip()
    try:
        data = json.loads(out_txt)
    except ValueError:
        raise BridgeError('BACKEND_OUTCOME_UNVERIFIED', 'Headless result is not JSON') from None
    if not isinstance(data, dict):
        raise BridgeError('BACKEND_OUTCOME_UNVERIFIED', 'Headless result is not an object')
    parsed_sid = data.get('session_id') or data.get('sessionId')
    if not isinstance(parsed_sid, str) or not parsed_sid or (session_id and parsed_sid != session_id):
        raise BridgeError('BACKEND_TARGET_MISMATCH', 'Headless result has no matching native identity')
    answer = data.get('result', data.get('answer', data.get('text')))
    if not isinstance(answer, str) or data.get('is_error') is True:
        raise BridgeError('BACKEND_OUTCOME_UNVERIFIED', 'Headless result is not a successful text outcome')
    media_paths = list(dict.fromkeys(MEDIA_PATH_RE.findall(answer)))
    return {
        "cancelled": False,
        "session_id": parsed_sid,
        "answer": answer,
        "commands": [],
        "file_changes": [],
        "media": media_paths,
        "afk_pilot_synced": False,
        "unresolved": [],
    }


_GROK_VERSION_CACHE: dict[str, str] = {}


def detect_grok_versions(grok_binary: str, extension_dir: str | None = None) -> tuple[str, str]:
    ext_ver = "unknown"
    candidates: list[Path] = []
    vscode_ext = Path.home() / ".vscode" / "extensions"
    if vscode_ext.exists():
        obsolete: dict = {}
        obs_file = vscode_ext / ".obsolete"
        if obs_file.is_file():
            try:
                loaded = json.loads(obs_file.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    obsolete = loaded
            except Exception:
                obsolete = {}
        dirs = [d for d in vscode_ext.glob("pawelhuryn.grok-vscode-phuryn-*") if d.is_dir() and (d / "package.json").is_file()]
        active = [d for d in dirs if not obsolete.get(d.name)] or dirs
        for d in sorted(active, key=lambda p: p.stat().st_mtime, reverse=True):
            candidates.append(d / "package.json")
    if extension_dir:
        candidates.append(Path(extension_dir) / "package.json")
    for pkg in candidates:
        if pkg.exists():
            try:
                data = json.loads(pkg.read_text(encoding="utf-8"))
                if isinstance(data.get("version"), str) and data["version"].strip():
                    ext_ver = data["version"].strip()
                    break
            except Exception:
                continue

    cli_ver = _GROK_VERSION_CACHE.get("cli_version")
    if not cli_ver:
        cli_ver = "unavailable"
        if grok_binary and Path(grok_binary).exists():
            import subprocess
            try:
                proc = subprocess.run(
                    [grok_binary, "--version"],
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
                    cli_ver = "unknown"
            except Exception:
                cli_ver = "unknown"
        _GROK_VERSION_CACHE["cli_version"] = cli_ver

    return ext_ver, cli_ver
