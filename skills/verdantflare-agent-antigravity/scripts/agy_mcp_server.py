#!/usr/bin/env python3
"""Antigravity (Gemini) MCP Server & CLI Driver for multi-agent orchestration.

Exposes the VS Code Google Antigravity extension (backed by local `agy.exe --hub`
LanguageServerService Connect-RPC) and headless `agy.exe` CLI as standard MCP tools
so external agents (Claude Code, Codex, etc.) can dispatch tasks into the VS Code
Antigravity foreground panel, monitor execution, and inspect artifacts.
"""
from __future__ import annotations

import argparse
import ctypes
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
from typing import Any
import urllib.error
import urllib.parse
import urllib.request
import uuid
from agy_jobs import JobError, call_tool as call_cli_job, tool_schemas as cli_job_schemas

SERVER_NAME = "verdantflare-agent-antigravity"
SERVER_VERSION = "1.0.0"
DEFAULT_MODEL = "MODEL_PLACEHOLDER_M418"
SERVICE_PREFIX = "/exa.language_server_pb.LanguageServerService/"
_CLI_ASYNC_STARTUP_CHECK_SEC = 6.5


def _gemini_home() -> Path:
    return Path(os.environ.get("AGY_GEMINI_HOME", "~/.gemini")).expanduser().resolve()


def _brain_root() -> Path:
    return _gemini_home() / "antigravity" / "brain"


def _agy_binary() -> Path:
    env_bin = os.environ.get("AGY_BINARY")
    if env_bin:
        return Path(env_bin).expanduser().resolve()
    candidate = _gemini_home() / "bin" / ("agy.exe" if os.name == "nt" else "agy")
    if candidate.is_file():
        return candidate
    return Path("agy.exe" if os.name == "nt" else "agy")


def path_to_workspace_uri(workspace_path: str | Path) -> str:
    """Convert a local filesystem path into the file:/// URI format expected by Antigravity Hub."""
    resolved = Path(workspace_path).expanduser().resolve()
    posix = resolved.as_posix()
    if re.match(r"^[A-Za-z]:/", posix):
        drive = posix[0].lower()
        rest = posix[2:]
        return f"file:///{drive}%3A{rest}"
    if posix.startswith("/"):
        return f"file://{posix}"
    return f"file:///{posix}"


def normalize_uri_for_compare(uri_or_path: str) -> str:
    """Normalize a file:/// URI or filesystem path for case-insensitive workspace matching."""
    text = urllib.parse.unquote(str(uri_or_path or "")).strip()
    if text.startswith("file:///"):
        text = text[len("file:///") :]
    elif text.startswith("file://"):
        text = text[len("file://") :]
    return text.replace("\\", "/").rstrip("/").lower()


def workspace_matches_uri(target_uri_or_path: str, candidate_uri_or_path: str) -> str | None:
    """Check if target and candidate workspace paths match exactly or by subdirectory prefix.

    Returns 'exact', 'prefix', or None.
    """
    t_norm = normalize_uri_for_compare(target_uri_or_path)
    c_norm = normalize_uri_for_compare(candidate_uri_or_path)
    if not t_norm or not c_norm:
        return None
    if t_norm == c_norm:
        return "exact"
    if t_norm.startswith(c_norm + "/") or c_norm.startswith(t_norm + "/"):
        return "prefix"
    return None


def _extract_project_resource_uris(project: dict[str, Any]) -> list[str]:
    """Extract all folder and gitFolder URIs configured on an Antigravity Project."""
    uris: list[str] = []

    def _collect_from_resources(res_list: list[dict[str, Any]] | None) -> None:
        for res in res_list or []:
            folder_uri = res.get("folderUri")
            if folder_uri:
                uris.append(folder_uri)
            git_uri = (res.get("gitFolder") or {}).get("folderUri")
            if git_uri:
                uris.append(git_uri)

    _collect_from_resources((project.get("projectResources") or {}).get("resources"))
    for env in (project.get("environments") or {}).get("environments") or []:
        _collect_from_resources((env.get("resources") or {}).get("resources"))
    return uris


def resolve_project_id(
    address: str,
    csrf_token: str,
    workspace_uri_or_path: str,
    summaries: dict[str, Any] | None = None,
) -> str | None:
    """Resolve the Antigravity Project ID matching workspace_uri_or_path so new cascades group under Projects."""
    raw = str(workspace_uri_or_path or "").strip()
    if not raw:
        return None
    canonical_input = raw if raw.startswith("file://") else path_to_workspace_uri(raw)
    target_norm = normalize_uri_for_compare(canonical_input)
    if not target_norm:
        return None
    home_norm = normalize_uri_for_compare(Path.home().as_posix())

    try:
        list_resp = lsrpc_call(address, csrf_token, "ListProjects", {}, timeout=3.0)
        raw_ids = list_resp.get("projectIds") or []
        valid_ids = [pid for pid in raw_ids if pid and pid != "outside-of-project"]
        if valid_ids:
            read_resp = lsrpc_call(address, csrf_token, "ReadProjects", {"ids": valid_ids}, timeout=3.0)
            best_prefix_pid: str | None = None
            best_prefix_len = -1
            for proj in read_resp.get("projects") or []:
                pid = proj.get("id")
                if not pid or pid == "outside-of-project" or proj.get("archived") is True:
                    continue
                for res_uri in _extract_project_resource_uris(proj):
                    res_norm = normalize_uri_for_compare(res_uri)
                    if not res_norm:
                        continue
                    if target_norm == res_norm:
                        return str(pid)
                    if res_norm != home_norm and target_norm.startswith(res_norm + "/"):
                        if len(res_norm) > best_prefix_len:
                            best_prefix_len = len(res_norm)
                            best_prefix_pid = str(pid)
            if best_prefix_pid:
                return best_prefix_pid
    except Exception:
        pass

    if summaries:
        ordered = sorted(
            summaries.values(),
            key=lambda item: item.get("lastModifiedTime") or "",
            reverse=True,
        )
        for info in ordered:
            meta = info.get("trajectoryMetadata") or {}
            pid = meta.get("projectId")
            if not pid or pid == "outside-of-project":
                continue
            candidate_uris = [
                w.get("workspaceFolderAbsoluteUri", "")
                for w in (info.get("workspaces") or [])
                if w.get("workspaceFolderAbsoluteUri")
            ] + list(meta.get("workspaceUris") or [])
            if any(workspace_matches_uri(canonical_input, u) == "exact" for u in candidate_uris):
                return str(pid)

    return None


def _query_windows_agy_processes() -> list[dict[str, Any]]:
    """Query running agy.exe processes on Windows, trying pwsh first then falling back to powershell.exe."""
    ps_cmd = (
        "Get-CimInstance Win32_Process -Filter \"Name='agy.exe'\" "
        "| Select-Object ProcessId, CommandLine | ConvertTo-Json -Compress"
    )
    for shell_bin in ("pwsh", "powershell.exe"):
        try:
            out = subprocess.check_output(
                [shell_bin, "-NoProfile", "-Command", ps_cmd],
                text=True,
                timeout=8,
                stderr=subprocess.DEVNULL,
            ).strip()
            if not out:
                return []
            parsed = json.loads(out)
            return [parsed] if isinstance(parsed, dict) else list(parsed)
        except Exception:
            continue
    return []


def lsrpc_call(
    address: str,
    csrf_token: str,
    method: str,
    payload: dict[str, Any] | None = None,
    timeout: float = 15.0,
) -> dict[str, Any]:
    """Invoke a Connect-RPC method on LanguageServerService."""
    url = f"http://{address}{SERVICE_PREFIX}{method}"
    body_bytes = json.dumps(payload or {}, ensure_ascii=False).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "x-codeium-csrf-token": csrf_token,
    }
    req = urllib.request.Request(url, data=body_bytes, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            return json.loads(raw) if raw.strip() else {}
    except urllib.error.HTTPError as exc:
        err_body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"LS RPC {method} HTTP {exc.code}: {err_body}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"LS RPC {method} connection error ({address}): {exc}") from exc


def _strip_outer_quotes(value: str) -> str:
    text = value.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in ('"', "'"):
        return text[1:-1]
    return text


def _tokenize_cmdline(cmdline: str) -> list[str]:
    """Split a Windows or POSIX command line into raw tokens without altering backslashes."""
    return re.findall(r'(?:"[^"]*"|\'[^\']*\'|\S+)+', cmdline or "")


def _extract_cmdline_flag_values(cmdline: str, flag_names: tuple[str, ...]) -> list[str]:
    """Extract repeatable `--flag=value` or `--flag value` arguments from a command line."""
    tokens = _tokenize_cmdline(cmdline)
    values: list[str] = []
    idx = 0
    while idx < len(tokens):
        tok = _strip_outer_quotes(tokens[idx])
        if tok in flag_names:
            if idx + 1 < len(tokens):
                nxt = _strip_outer_quotes(tokens[idx + 1])
                if nxt and not nxt.startswith("--"):
                    values.append(nxt)
                    idx += 2
                    continue
        else:
            for flag in flag_names:
                prefix = flag + "="
                if tok.startswith(prefix):
                    rhs = _strip_outer_quotes(tok[len(prefix) :])
                    if rhs:
                        values.append(rhs)
                    break
        idx += 1
    return values


def _has_hub_flag(cmdline: str) -> bool:
    for raw_tok in _tokenize_cmdline(cmdline):
        tok = _strip_outer_quotes(raw_tok)
        if tok == "--hub" or tok.startswith("--hub-port") or tok.startswith("--hub_port"):
            return True
    return False


def _resolve_app_data_dir(app_data_arg: str | None = None) -> Path:
    raw = (app_data_arg or "").strip() or "antigravity"
    candidate = Path(raw).expanduser()
    return candidate.resolve() if candidate.is_absolute() else (_gemini_home() / candidate).resolve()


def _read_hub_discovery_file(port: int | str, app_data_dir: Path | None = None) -> dict[str, Any] | None:
    """Read CSRF token and metadata from `~/.gemini/<app_data_dir>/daemon/hub_<port>.json`."""
    base_dir = app_data_dir or _resolve_app_data_dir()
    disc_file = base_dir / "daemon" / f"hub_{port}.json"
    if not disc_file.is_file():
        return None
    try:
        data = json.loads(disc_file.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    csrf = str(data.get("csrfToken") or "").strip()
    if not csrf:
        return None
    http_port = data.get("httpPort")
    if http_port is not None and str(http_port) != str(port):
        return None
    return data


def _is_pid_alive(pid: int | None) -> bool:
    """Check if an OS process ID is currently alive without sending signals."""
    if not isinstance(pid, int) or pid <= 0:
        return False
    if os.name == "nt":
        try:
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
            kernel32.OpenProcess.restype = ctypes.c_void_p
            kernel32.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
            kernel32.GetExitCodeProcess.restype = ctypes.c_int
            kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
            kernel32.CloseHandle.restype = ctypes.c_int

            handle = kernel32.OpenProcess(0x1000, 0, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
            if not handle:
                return ctypes.get_last_error() == 5  # ERROR_ACCESS_DENIED => alive
            try:
                exit_code = ctypes.c_uint32(0)
                ok = kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
                return bool(ok and exit_code.value == 259)  # STILL_ACTIVE
            finally:
                kernel32.CloseHandle(handle)
        except Exception:
            return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False


def _parse_hub_process(pid: int | None, cmdline: str) -> dict[str, Any] | None:
    """Parse an `agy --hub` process command line, resolving CSRF token from CLI or daemon discovery JSON."""
    if not _has_hub_flag(cmdline):
        return None
    ports = _extract_cmdline_flag_values(cmdline, ("--hub-port", "--hub_port"))
    if not ports or not ports[0].isdigit():
        return None
    port_str = ports[0]
    csrfs = _extract_cmdline_flag_values(cmdline, ("--csrf_token", "--csrf-token"))
    app_dirs = _extract_cmdline_flag_values(cmdline, ("--app_data_dir", "--app-data-dir"))
    app_data_path = _resolve_app_data_dir(app_dirs[0] if app_dirs else None)

    mounted_workspaces: list[str] = []
    for raw_dir in _extract_cmdline_flag_values(cmdline, ("--add-dir",)):
        try:
            uri = path_to_workspace_uri(raw_dir)
            if uri not in mounted_workspaces:
                mounted_workspaces.append(uri)
        except Exception:
            continue

    csrf_token = csrfs[0] if csrfs else ""
    source = "process"
    disc: dict[str, Any] | None = None
    if not csrf_token:
        disc = _read_hub_discovery_file(port_str, app_data_path)
        if disc:
            csrf_token = str(disc["csrfToken"]).strip()
            source = "process+daemon"
    if not csrf_token:
        return None

    return {
        "pid": pid,
        "address": f"localhost:{port_str}",
        "port": int(port_str),
        "csrf_token": csrf_token,
        "source": source,
        "mounted_workspaces": mounted_workspaces,
        "ls_version": (disc or {}).get("lsVersion"),
    }


def _scan_daemon_hub_files(
    seen_ports: set[str],
    excluded_pids: set[int],
    app_data_dir: Path | None = None,
) -> list[dict[str, Any]]:
    """Fallback scan of `daemon/hub_*.json` when OS process enumeration yields no Hub entries."""
    daemon_dir = (app_data_dir or _resolve_app_data_dir()) / "daemon"
    if not daemon_dir.is_dir():
        return []
    found: list[dict[str, Any]] = []
    for jf in sorted(daemon_dir.glob("hub_*.json")):
        try:
            data = json.loads(jf.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        http_port = data.get("httpPort")
        csrf_token = str(data.get("csrfToken") or "").strip()
        pid = data.get("pid")
        if not csrf_token or not isinstance(http_port, int) or http_port <= 0:
            continue
        port_str = str(http_port)
        if port_str in seen_ports:
            continue
        if isinstance(pid, int):
            if pid in excluded_pids or not _is_pid_alive(pid):
                continue
        seen_ports.add(port_str)
        found.append(
            {
                "pid": pid if isinstance(pid, int) else None,
                "address": f"localhost:{port_str}",
                "port": http_port,
                "csrf_token": csrf_token,
                "source": "daemon",
                "mounted_workspaces": [],
                "ls_version": data.get("lsVersion"),
            }
        )
    return found


def discover_hubs() -> list[dict[str, Any]]:
    """Discover all running `agy.exe --hub` instances and enrich with trajectory summaries."""
    candidates: list[dict[str, Any]] = []
    seen_ports: set[str] = set()
    non_hub_agy_pids: set[int] = set()
    process_scan_found_hub = False

    env_addr = os.environ.get("ANTIGRAVITY_LS_ADDRESS", "").strip()
    env_csrf = os.environ.get("ANTIGRAVITY_CSRF_TOKEN", "").strip()
    if env_addr:
        port_str = env_addr.split(":")[-1]
        effective_csrf = env_csrf
        source = "env"
        if not effective_csrf and port_str.isdigit():
            disc = _read_hub_discovery_file(port_str)
            if disc:
                effective_csrf = str(disc["csrfToken"]).strip()
                source = "env+daemon"
        if effective_csrf:
            seen_ports.add(port_str)
            candidates.append(
                {
                    "pid": None,
                    "address": env_addr,
                    "port": int(port_str) if port_str.isdigit() else 0,
                    "csrf_token": effective_csrf,
                    "source": source,
                    "mounted_workspaces": [],
                }
            )

    if os.name == "nt":
        for proc in _query_windows_agy_processes():
            raw_pid = proc.get("ProcessId")
            pid = int(raw_pid) if isinstance(raw_pid, int) or (isinstance(raw_pid, str) and raw_pid.isdigit()) else None
            cmdline = proc.get("CommandLine") or ""
            parsed = _parse_hub_process(pid, cmdline)
            if parsed is not None:
                process_scan_found_hub = True
                port_str = str(parsed["port"])
                if port_str not in seen_ports:
                    seen_ports.add(port_str)
                    candidates.append(parsed)
            elif pid is not None:
                non_hub_agy_pids.add(pid)
    else:
        try:
            out = subprocess.check_output(["ps", "-eo", "pid,args"], text=True, timeout=5)
            for line in out.splitlines():
                pid_m = re.match(r"^\s*(\d+)\s+(.*)$", line)
                if not pid_m:
                    continue
                pid = int(pid_m.group(1))
                args_str = pid_m.group(2)
                parsed = _parse_hub_process(pid, args_str)
                if parsed is not None:
                    process_scan_found_hub = True
                    port_str = str(parsed["port"])
                    if port_str not in seen_ports:
                        seen_ports.add(port_str)
                        candidates.append(parsed)
                elif re.search(r"(?:^|[\\/])agy(?:\.exe)?(?:\s|$)", args_str):
                    non_hub_agy_pids.add(pid)
        except Exception:
            pass

    if not process_scan_found_hub:
        candidates.extend(_scan_daemon_hub_files(seen_ports, non_hub_agy_pids))

    alive_hubs: list[dict[str, Any]] = []
    for item in candidates:
        try:
            trajs = lsrpc_call(
                item["address"],
                item["csrf_token"],
                "GetAllCascadeTrajectories",
                {"excludeSubtrajectories": True},
                timeout=3.0,
            )
            summaries = trajs.get("trajectorySummaries") or {}
            mounted_ws = set(item.get("mounted_workspaces") or [])
            workspaces: set[str] = set(mounted_ws)
            latest_ts = ""
            running_count = 0
            for cid, summary in summaries.items():
                mtime = summary.get("lastModifiedTime") or ""
                if mtime > latest_ts:
                    latest_ts = mtime
                if summary.get("status") == "CASCADE_RUN_STATUS_RUNNING":
                    running_count += 1
                for ws in summary.get("workspaces") or []:
                    uri = ws.get("workspaceFolderAbsoluteUri")
                    if uri:
                        workspaces.add(uri)
            item["conversation_count"] = len(summaries)
            item["running_count"] = running_count
            item["latest_modified_time"] = latest_ts
            item["mounted_workspaces"] = sorted(mounted_ws)
            item["workspaces"] = sorted(workspaces)
            item["summaries"] = summaries
            alive_hubs.append(item)
        except Exception:
            continue

    alive_hubs.sort(
        key=lambda h: (
            h.get("running_count", 0) > 0,
            h.get("latest_modified_time", ""),
            h.get("conversation_count", 0),
        ),
        reverse=True,
    )
    return alive_hubs


def select_hub(
    conversation_id: str | None = None,
    workspace: str | None = None,
    hubs: list[dict[str, Any]] | None = None,
    strict_workspace: bool = False,
) -> dict[str, Any]:
    """Select the best matching running Antigravity Hub for the given conversation or workspace.

    Attaches `workspace_matched` (bool) and `matched_by` (str) metadata onto the returned hub dict.
    """
    active_hubs = hubs if hubs is not None else discover_hubs()
    if not active_hubs:
        raise JobError(
            "OFFLINE",
            "No running Antigravity Hub (`agy.exe --hub`) found. "
            "Ensure the VS Code Google Antigravity panel is open or use mode='cli'.",
        )
    if conversation_id:
        for hub in active_hubs:
            if conversation_id in (hub.get("summaries") or {}):
                if strict_workspace and workspace and not any(
                    workspace_matches_uri(path_to_workspace_uri(workspace), uri)
                    for uri in hub.get("workspaces", [])
                ):
                    raise JobError("WORKSPACE_DENIED")
                chosen = dict(hub)
                chosen["workspace_matched"] = True
                chosen["matched_by"] = "conversation_id"
                return chosen
    if workspace:
        target_uri = path_to_workspace_uri(workspace)
        # 1a. Exact match on mounted `--add-dir` workspaces first
        for hub in active_hubs:
            for ws_uri in hub.get("mounted_workspaces") or []:
                if workspace_matches_uri(target_uri, ws_uri) == "exact":
                    chosen = dict(hub)
                    chosen["workspace_matched"] = True
                    chosen["matched_by"] = "workspace_exact"
                    return chosen
        # 1b. Exact match on all known hub workspaces
        for hub in active_hubs:
            for ws_uri in hub.get("workspaces") or []:
                if workspace_matches_uri(target_uri, ws_uri) == "exact":
                    chosen = dict(hub)
                    chosen["workspace_matched"] = True
                    chosen["matched_by"] = "workspace_exact"
                    return chosen
        # 2a. Subdirectory / parent prefix match on mounted workspaces first
        for hub in active_hubs:
            for ws_uri in hub.get("mounted_workspaces") or []:
                if workspace_matches_uri(target_uri, ws_uri) == "prefix":
                    chosen = dict(hub)
                    chosen["workspace_matched"] = True
                    chosen["matched_by"] = "workspace_prefix"
                    return chosen
        # 2b. Subdirectory / parent prefix match on all known hub workspaces
        for hub in active_hubs:
            for ws_uri in hub.get("workspaces") or []:
                if workspace_matches_uri(target_uri, ws_uri) == "prefix":
                    chosen = dict(hub)
                    chosen["workspace_matched"] = True
                    chosen["matched_by"] = "workspace_prefix"
                    return chosen
        if strict_workspace:
            known_ws = [h.get("workspaces") for h in active_hubs]
            raise RuntimeError(
                f"No running Antigravity Hub matched workspace {target_uri!r}. "
                f"Active hub workspaces: {known_ws}"
            )
        # 3. Prefer a freshly opened window with 0 existing conversations before falling back to hubs[0]
        for hub in active_hubs:
            if not (hub.get("workspaces") or []):
                chosen = dict(hub)
                chosen["workspace_matched"] = False
                chosen["matched_by"] = "empty_hub_fallback"
                return chosen

    chosen = dict(active_hubs[0])
    chosen["workspace_matched"] = workspace is None
    chosen["matched_by"] = "default_fallback"
    return chosen


def list_brain_artifacts(conversation_id: str) -> list[str]:
    """List user-facing artifact files generated inside brain/<conversation_id>/."""
    conv_dir = _brain_root() / conversation_id
    if not conv_dir.is_dir():
        return []
    artifacts: list[str] = []
    for p in sorted(conv_dir.rglob("*")):
        if not p.is_file():
            continue
        rel_parts = p.relative_to(conv_dir).parts
        if any(part.startswith(".") for part in rel_parts):
            continue
        if p.name.endswith(".metadata.json") or p.name.endswith(".resolved") or ".resolved." in p.name:
            continue
        artifacts.append(p.as_posix())
    return artifacts


def extract_trajectory_digest(traj_resp: dict[str, Any]) -> dict[str, Any]:
    """Extract the latest assistant response, thinking, tool calls, and status from GetCascadeTrajectory."""
    status = traj_resp.get("status", "UNKNOWN")
    traj = traj_resp.get("trajectory") or {}
    steps = traj.get("steps") or []
    last_response = ""
    last_thinking = ""
    recent_tool_calls: list[dict[str, Any]] = []
    last_user_input = ""

    for idx, step in enumerate(steps):
        stype = step.get("type", "")
        if stype == "CORTEX_STEP_TYPE_USER_INPUT":
            items = (step.get("userInput") or {}).get("items") or []
            texts = [it.get("text", "") for it in items if it.get("text")]
            if texts:
                last_user_input = "\n".join(texts)
        elif stype == "CORTEX_STEP_TYPE_PLANNER_RESPONSE":
            pr = step.get("plannerResponse") or {}
            resp_text = pr.get("modifiedResponse") or pr.get("response") or ""
            if resp_text:
                last_response = resp_text
            if pr.get("thinking"):
                last_thinking = pr.get("thinking")
            for tc in pr.get("toolCalls") or []:
                recent_tool_calls.append(
                    {
                        "step_index": idx,
                        "name": tc.get("name"),
                        "arguments_preview": (tc.get("argumentsJson") or "")[:300],
                    }
                )

    return {
        "status": status,
        "num_total_steps": traj_resp.get("numTotalSteps", len(steps)),
        "last_user_input": last_user_input,
        "last_response": last_response,
        "last_thinking": last_thinking[:1000] if last_thinking else "",
        "recent_tool_calls": recent_tool_calls[-10:],
    }


def open_conversation_in_vscode_panel(conversation_id: str) -> bool:
    """Trigger VS Code URI handler to switch the foreground Antigravity panel to conversation_id."""
    uri = f"vscode://google.google-antigravity/open-conversation?cascadeId={conversation_id}"
    try:
        if os.name == "nt":
            subprocess.Popen(
                ["cmd.exe", "/d", "/c", "start", "", uri],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        elif sys.platform == "darwin":
            subprocess.Popen(["open", uri], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            subprocess.Popen(["xdg-open", uri], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except Exception:
        return False


def tool_agy_hub_status() -> dict[str, Any]:
    """Report status of all running Antigravity Hubs, available models, and binary location."""
    hubs = discover_hubs()
    models_info: list[dict[str, Any]] = []
    if hubs:
        primary = hubs[0]
        try:
            mdata = lsrpc_call(
                primary["address"],
                primary["csrf_token"],
                "GetCascadeModelConfigData",
                {},
                timeout=5.0,
            )
            for cfg in mdata.get("clientModelConfigs") or []:
                models_info.append(
                    {
                        "label": cfg.get("label"),
                        "model": (cfg.get("modelOrAlias") or {}).get("model"),
                        "remaining_fraction": (cfg.get("quotaInfo") or {}).get("remainingFraction"),
                        "reset_time": (cfg.get("quotaInfo") or {}).get("resetTime"),
                    }
                )
        except Exception:
            pass

    sanitized_hubs = [
        {
            "pid": h.get("pid"),
            "address": h.get("address"),
            "port": h.get("port"),
            "source": h.get("source"),
            "ls_version": h.get("ls_version"),
            "conversation_count": h.get("conversation_count"),
            "running_count": h.get("running_count"),
            "latest_modified_time": h.get("latest_modified_time"),
            "mounted_workspaces": h.get("mounted_workspaces") or [],
            "workspaces": h.get("workspaces"),
        }
        for h in hubs
    ]
    return {
        "ok": len(hubs) > 0,
        "agy_binary": _agy_binary().as_posix(),
        "brain_root": _brain_root().as_posix(),
        "hub_count": len(hubs),
        "hubs": sanitized_hubs,
        "models": models_info,
    }


def tool_agy_list_conversations(
    workspace: str | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    """List conversations from running Antigravity Hubs, optionally filtered by workspace."""
    hubs = discover_hubs()
    target_uri = path_to_workspace_uri(workspace) if workspace else None
    items: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    for hub in hubs:
        summaries = hub.get("summaries") or {}
        for cid, info in summaries.items():
            if cid in seen_ids:
                continue
            meta = info.get("trajectoryMetadata") or {}
            raw_ws = [
                w.get("workspaceFolderAbsoluteUri", "")
                for w in (info.get("workspaces") or [])
                if w.get("workspaceFolderAbsoluteUri")
            ] + [u for u in (meta.get("workspaceUris") or []) if u]
            ws_list = list(dict.fromkeys(raw_ws))
            effective_ws_for_filter = ws_list or list(hub.get("mounted_workspaces") or [])
            if target_uri:
                if not any(workspace_matches_uri(target_uri, u) is not None for u in effective_ws_for_filter):
                    continue
            seen_ids.add(cid)
            proj_id = meta.get("projectId") or None
            items.append(
                {
                    "conversation_id": cid,
                    "summary": info.get("summary", "(Untitled)"),
                    "status": info.get("status", "UNKNOWN"),
                    "step_count": info.get("stepCount", 0),
                    "created_time": info.get("createdTime", ""),
                    "last_modified_time": info.get("lastModifiedTime", ""),
                    "project_id": proj_id,
                    "workspaces": ws_list,
                    "hub_address": hub["address"],
                }
            )

    items.sort(key=lambda x: x.get("last_modified_time", ""), reverse=True)
    selected = items[: max(1, int(limit))]
    for entry in selected:
        entry["artifacts"] = list_brain_artifacts(entry["conversation_id"])
    return {
        "total": len(items),
        "conversations": selected,
    }


def _cli_dispatch_dir() -> Path:
    d = _gemini_home() / "antigravity" / "cli_dispatch"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _safe_unlink(path: Path | None) -> None:
    if path is None:
        return
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def _prune_cli_dispatch_dir(dispatch_dir: Path, keep_recent: int = 40) -> None:
    try:
        files = sorted(
            (p for p in dispatch_dir.iterdir() if p.is_file()),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
    except OSError:
        return
    for old_file in files[keep_recent:]:
        _safe_unlink(old_file)


def _resolve_executable_agy_binary() -> tuple[str | None, str | None]:
    candidate = _agy_binary()
    if candidate.is_absolute() or candidate.parent != Path("."):
        if candidate.is_file():
            return str(candidate), None
        return None, f"Antigravity CLI binary not found at {candidate.as_posix()}"
    found = shutil.which(str(candidate))
    if found:
        return found, None
    return None, f"Antigravity CLI binary {str(candidate)!r} not found in PATH or ~/.gemini/bin"


def _parse_cli_output(stdout_text: str) -> dict[str, Any]:
    """Parse `stream-json` or `json` CLI stdout for terminal conversation_id, status, response, and error."""
    conv_id = ""
    status = ""
    response_text = ""
    err_text = ""
    found_json = False

    for line in (stdout_text or "").splitlines():
        raw = line.strip()
        if not raw:
            continue
        try:
            obj = json.loads(raw)
        except ValueError:
            continue
        if not isinstance(obj, dict):
            continue
        found_json = True
        if obj.get("event") == "result" and isinstance(obj.get("result"), dict):
            res = obj["result"]
            conv_id = str(res.get("conversation_id") or conv_id)
            status = str(res.get("status") or status)
            if isinstance(res.get("response"), str):
                response_text = res["response"]
            if res.get("error"):
                err_text = str(res["error"])
        elif obj.get("event") == "error":
            err_text = str(obj.get("error") or obj.get("message") or err_text)
        elif "status" in obj or "conversation_id" in obj or "error" in obj:
            conv_id = str(obj.get("conversation_id") or conv_id)
            status = str(obj.get("status") or status)
            if isinstance(obj.get("response"), str):
                response_text = obj["response"]
            if obj.get("error"):
                err_text = str(obj["error"])

    return {
        "found_json": found_json,
        "conversation_id": conv_id.strip(),
        "status": status.strip(),
        "response": response_text,
        "error": err_text.strip(),
    }


def _read_log_error_tail(log_path: Path | None, max_chars: int = 1200) -> str:
    if log_path is None or not log_path.is_file():
        return ""
    try:
        lines = [ln.strip() for ln in log_path.read_text(encoding="utf-8", errors="replace").splitlines() if ln.strip()]
    except Exception:
        return ""
    if not lines:
        return ""
    err_lines = [
        ln
        for ln in lines
        if (
            ln.startswith(("E", "F"))
            or any(k in ln.lower() for k in ("error", "fatal", "panic", "failed", "unauthenticated", "denied"))
        )
        and "cs path /usr/bin/cs invalid" not in ln
        and "not found in any store: file does not exist" not in ln
    ]
    chosen = err_lines[-6:] if err_lines else lines[-5:]
    return " | ".join(chosen)[:max_chars]


def _format_cli_failure(
    returncode: int | None,
    parsed: dict[str, Any],
    stdout_text: str,
    stderr_text: str,
    log_path: Path | None = None,
) -> str:
    if parsed.get("error"):
        return str(parsed["error"]).strip()
    stderr_clean = (stderr_text or "").strip()
    if stderr_clean:
        return stderr_clean[:2000]
    if parsed.get("status") and parsed["status"] != "SUCCESS":
        return f"Antigravity CLI reported status={parsed['status']!r} (exit code {returncode}) without starting a valid turn."
    stdout_clean = (stdout_text or "").strip()
    if stdout_clean and not parsed.get("found_json"):
        return f"Antigravity CLI exited with code {returncode}: {stdout_clean[:1000]}"
    log_tail = _read_log_error_tail(log_path)
    if log_tail:
        return f"Antigravity CLI exited (code {returncode}) without generating a conversation; log tail: {log_tail}"
    return f"Antigravity CLI exited (code {returncode}) immediately without generating a conversation or diagnostic output."


def tool_agy_dispatch_task(
    prompt: str,
    conversation_id: str | None = None,
    workspace: str | None = None,
    project_id: str | None = None,
    mode: str = "panel",
    focus_panel: bool = False,
    wait: bool = True,
    timeout_sec: int = 300,
    model: str = DEFAULT_MODEL,
    skip_permissions: bool = False,
    strict_workspace: bool = False,
) -> dict[str, Any]:
    """Dispatch a prompt to Antigravity via VS Code foreground panel injection (default) or CLI."""
    if not prompt or not prompt.strip():
        raise ValueError("prompt must be a non-empty string")

    effective_ws = workspace or os.getcwd()
    effective_ws_path = Path(effective_ws).expanduser().resolve()
    ws_uri = path_to_workspace_uri(effective_ws_path)

    if mode == "cli":
        if os.environ.get("VERDANTFLARE_AGENT_ANTIGRAVITY_DEPTH"):
            raise JobError("RECURSIVE_DELEGATION_DENIED", "Nested Antigravity CLI dispatch is forbidden.")
        if not effective_ws_path.is_dir():
            return {
                "ok": False,
                "mode": "cli",
                "returncode": None,
                "error": f"Workspace directory does not exist: {effective_ws_path.as_posix()}",
                "project_id": project_id,
                "workspace_uri": ws_uri,
                "skip_permissions": skip_permissions,
            }
        agy_exec, bin_err = _resolve_executable_agy_binary()
        if not agy_exec:
            return {
                "ok": False,
                "mode": "cli",
                "returncode": None,
                "error": bin_err or "Antigravity CLI binary not found.",
                "project_id": project_id,
                "workspace_uri": ws_uri,
                "skip_permissions": skip_permissions,
            }

        dispatch_dir = _cli_dispatch_dir()
        _prune_cli_dispatch_dir(dispatch_dir)
        run_tag = f"cli_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}"
        ls_log_file = dispatch_dir / f"{run_tag}.ls.log"

        cmd = [
            agy_exec,
            "--input-format",
            "stream-json",
            "--output-format",
            "stream-json",
            "--log-file",
            str(ls_log_file),
        ]
        if skip_permissions:
            cmd.append("--dangerously-skip-permissions")
        if model and model != DEFAULT_MODEL:
            cmd.extend(["--model", model])
        if conversation_id:
            cmd.extend(["--conversation", conversation_id])
        elif project_id:
            cmd.extend(["--project", project_id])
        if workspace:
            cmd.extend(["--add-dir", str(effective_ws_path)])

        stdin_payload = (
            json.dumps(
                {"event": "user", "message": {"role": "user", "content": prompt}},
                ensure_ascii=False,
            )
            + "\n"
        )
        child_env = dict(os.environ, VERDANTFLARE_AGENT_ANTIGRAVITY_DEPTH="1")

        if not wait:
            stdin_file = dispatch_dir / f"{run_tag}.stdin.jsonl"
            stdout_file = dispatch_dir / f"{run_tag}.stdout.jsonl"
            stderr_file = dispatch_dir / f"{run_tag}.stderr.log"
            stdin_file.write_text(stdin_payload, encoding="utf-8")

            popen_kwargs: dict[str, Any] = {
                "cwd": str(effective_ws_path),
                "env": child_env,
            }
            if os.name == "nt":
                popen_kwargs["creationflags"] = (
                    getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
                    | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
                )
            else:
                popen_kwargs["start_new_session"] = True

            try:
                with (
                    stdin_file.open("rb") as in_fh,
                    stdout_file.open("wb") as out_fh,
                    stderr_file.open("wb") as err_fh,
                ):
                    proc = subprocess.Popen(
                        cmd,
                        stdin=in_fh,
                        stdout=out_fh,
                        stderr=err_fh,
                        **popen_kwargs,
                    )
            except Exception as exc:
                _safe_unlink(stdin_file)
                return {
                    "ok": False,
                    "mode": "cli",
                    "returncode": None,
                    "error": f"Failed to launch Antigravity CLI: {exc}",
                    "project_id": project_id,
                    "workspace_uri": ws_uri,
                    "skip_permissions": skip_permissions,
                }

            check_deadline = time.monotonic() + max(0.0, float(_CLI_ASYNC_STARTUP_CHECK_SEC))
            while time.monotonic() < check_deadline:
                if proc.poll() is not None:
                    break
                time.sleep(0.1)

            if proc.poll() is not None:
                _safe_unlink(stdin_file)
                stdout_text = stdout_file.read_text(encoding="utf-8", errors="replace") if stdout_file.is_file() else ""
                stderr_text = stderr_file.read_text(encoding="utf-8", errors="replace") if stderr_file.is_file() else ""
                parsed = _parse_cli_output(stdout_text)
                if proc.returncode == 0 and parsed["status"] == "SUCCESS" and bool(parsed["conversation_id"]):
                    _safe_unlink(ls_log_file)
                    return {
                        "ok": True,
                        "mode": "cli",
                        "async_pid": proc.pid,
                        "returncode": 0,
                        "status": "SUCCESS",
                        "conversation_id": parsed["conversation_id"],
                        "last_response": parsed["response"],
                        "stdout": stdout_text.strip(),
                        "stderr": stderr_text.strip(),
                        "project_id": project_id,
                        "workspace_uri": ws_uri,
                        "skip_permissions": skip_permissions,
                    }
                err_msg = _format_cli_failure(proc.returncode, parsed, stdout_text, stderr_text, ls_log_file)
                return {
                    "ok": False,
                    "mode": "cli",
                    "async_pid": proc.pid,
                    "returncode": proc.returncode,
                    "status": parsed["status"] or "ERROR",
                    "conversation_id": parsed["conversation_id"] or conversation_id,
                    "error": err_msg,
                    "stdout": stdout_text.strip(),
                    "stderr": stderr_text.strip(),
                    "stdout_path": stdout_file.as_posix(),
                    "stderr_path": stderr_file.as_posix(),
                    "log_path": ls_log_file.as_posix(),
                    "project_id": project_id,
                    "workspace_uri": ws_uri,
                    "skip_permissions": skip_permissions,
                }

            return {
                "ok": True,
                "mode": "cli",
                "async_pid": proc.pid,
                "status": "RUNNING",
                "conversation_id": conversation_id,
                "project_id": project_id,
                "workspace_uri": ws_uri,
                "skip_permissions": skip_permissions,
                "stdout_path": stdout_file.as_posix(),
                "stderr_path": stderr_file.as_posix(),
                "log_path": ls_log_file.as_posix(),
            }

        eff_timeout = max(10, int(timeout_sec))
        run_kwargs: dict[str, Any] = {}
        if os.name == "nt":
            run_kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        try:
            proc_res = subprocess.run(
                cmd,
                input=stdin_payload,
                cwd=str(effective_ws_path),
                env=child_env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=eff_timeout,
                check=False,
                **run_kwargs,
            )
        except subprocess.TimeoutExpired as exc:
            out_partial = (exc.stdout or "") if isinstance(exc.stdout, str) else ""
            err_partial = (exc.stderr or "") if isinstance(exc.stderr, str) else ""
            _safe_unlink(ls_log_file)
            return {
                "ok": False,
                "mode": "cli",
                "timed_out": True,
                "returncode": None,
                "error": f"Antigravity CLI timed out after {eff_timeout}s.",
                "stdout": out_partial.strip(),
                "stderr": err_partial.strip(),
                "project_id": project_id,
                "workspace_uri": ws_uri,
                "skip_permissions": skip_permissions,
            }
        except Exception as exc:
            _safe_unlink(ls_log_file)
            return {
                "ok": False,
                "mode": "cli",
                "returncode": None,
                "error": f"Failed to execute Antigravity CLI: {exc}",
                "project_id": project_id,
                "workspace_uri": ws_uri,
                "skip_permissions": skip_permissions,
            }

        parsed = _parse_cli_output(proc_res.stdout)
        is_ok = proc_res.returncode == 0 and parsed["status"] == "SUCCESS" and bool(parsed["conversation_id"])
        if is_ok:
            _safe_unlink(ls_log_file)
            return {
                "ok": True,
                "mode": "cli",
                "returncode": 0,
                "status": "SUCCESS",
                "conversation_id": parsed["conversation_id"],
                "last_response": parsed["response"],
                "stdout": proc_res.stdout.strip(),
                "stderr": proc_res.stderr.strip(),
                "project_id": project_id,
                "workspace_uri": ws_uri,
                "skip_permissions": skip_permissions,
            }

        err_msg = _format_cli_failure(proc_res.returncode, parsed, proc_res.stdout, proc_res.stderr, ls_log_file)
        _safe_unlink(ls_log_file)
        return {
            "ok": False,
            "mode": "cli",
            "returncode": proc_res.returncode,
            "status": parsed["status"] or "ERROR",
            "conversation_id": parsed["conversation_id"] or conversation_id,
            "error": err_msg,
            "stdout": proc_res.stdout.strip(),
            "stderr": proc_res.stderr.strip(),
            "project_id": project_id,
            "workspace_uri": ws_uri,
            "skip_permissions": skip_permissions,
        }

    # Default mode == "panel": inject into live VS Code Antigravity Hub
    hub = select_hub(
        conversation_id=conversation_id,
        workspace=effective_ws,
        strict_workspace=strict_workspace,
    )
    addr = hub["address"]
    csrf = hub["csrf_token"]
    created_new = False
    baseline_steps = 0
    effective_project_id: str | None = project_id

    if not conversation_id:
        if not effective_project_id:
            effective_project_id = resolve_project_id(
                addr,
                csrf,
                ws_uri,
                summaries=hub.get("summaries"),
            )
        start_payload: dict[str, Any] = {
            "source": "CORTEX_TRAJECTORY_SOURCE_CASCADE_CLIENT",
            "trajectoryType": "CORTEX_TRAJECTORY_TYPE_CASCADE",
        }
        if effective_project_id:
            start_payload["projectEnvConfig"] = {
                "projectId": effective_project_id,
                "defaultProjectEnvironment": {},
            }
        else:
            start_payload["workspaceUris"] = [ws_uri]
        start_resp = lsrpc_call(
            addr,
            csrf,
            "StartCascade",
            start_payload,
        )
        conversation_id = start_resp.get("cascadeId")
        if not conversation_id:
            raise RuntimeError(f"StartCascade did not return cascadeId: {start_resp}")
        created_new = True
    else:
        summary_info = (hub.get("summaries") or {}).get(conversation_id) or {}
        if not effective_project_id:
            effective_project_id = (summary_info.get("trajectoryMetadata") or {}).get("projectId") or None
        baseline_steps = int(summary_info.get("stepCount", 0))
        try:
            pre_traj = lsrpc_call(addr, csrf, "GetCascadeTrajectory", {"cascadeId": conversation_id}, timeout=5.0)
            pre_steps = (pre_traj.get("trajectory") or {}).get("steps") or []
            baseline_steps = max(baseline_steps, int(pre_traj.get("numTotalSteps", len(pre_steps))))
        except Exception:
            raise JobError('OBSERVATION_UNAVAILABLE') from None
        if pre_traj.get('status') != 'CASCADE_RUN_STATUS_IDLE':
            raise JobError('SESSION_BUSY')

    try:
        lsrpc_call(addr, csrf, "SetBrowserOpenConversation", {"cascadeId": conversation_id}, timeout=3.0)
    except Exception:
        pass

    focused_via_uri = False
    if focus_panel:
        focused_via_uri = open_conversation_in_vscode_panel(conversation_id)

    send_payload: dict[str, Any] = {
        "cascadeId": conversation_id,
        "items": [{"text": prompt}],
        "cascadeConfig": {
            "plannerConfig": {
                "conversational": {},
                "planModel": model or DEFAULT_MODEL,
            }
        },
        "blocking": False,
    }
    lsrpc_call(addr, csrf, "SendUserCascadeMessage", send_payload, timeout=15.0)

    result: dict[str, Any] = {
        "ok": True,
        "dispatched": True,
        "mode": "panel",
        "hub_address": addr,
        "hub_workspaces": hub.get("workspaces") or [],
        "workspace_matched": bool(hub.get("workspace_matched", True)),
        "matched_by": hub.get("matched_by", "default_fallback"),
        "conversation_id": conversation_id,
        "project_id": effective_project_id,
        "created_new": created_new,
        "baseline_steps": baseline_steps,
        "focused_panel": focused_via_uri,
        "workspace_uri": ws_uri,
    }

    if not wait:
        result["status"] = "CASCADE_RUN_STATUS_RUNNING"
        result["timed_out"] = False
        return result

    deadline = time.time() + max(5, int(timeout_sec))
    last_digest: dict[str, Any] = {}
    saw_running = False
    completed = False
    while time.time() < deadline:
        time.sleep(2.0)
        try:
            traj = lsrpc_call(addr, csrf, "GetCascadeTrajectory", {"cascadeId": conversation_id}, timeout=10.0)
        except Exception:
            continue
        last_digest = extract_trajectory_digest(traj)
        st = last_digest.get("status")
        cur_steps = int(last_digest.get("num_total_steps", 0))
        if st == "CASCADE_RUN_STATUS_RUNNING":
            saw_running = True
        elif st == "CASCADE_RUN_STATUS_IDLE" and (saw_running or cur_steps >= baseline_steps + 2):
            completed = True
            break

    timed_out = not completed
    result.update(last_digest)
    result["timed_out"] = timed_out
    if timed_out:
        result["ok"] = False
        result["error"] = f"Timed out after {max(5, int(timeout_sec))}s waiting for conversation {conversation_id} to become idle."
    result["artifacts"] = list_brain_artifacts(conversation_id)
    return result


def tool_agy_get_conversation(
    conversation_id: str,
    wait_sec: int = 0,
) -> dict[str, Any]:
    """Get execution status, latest assistant response, and artifacts for conversation_id."""
    if not conversation_id:
        raise ValueError("conversation_id is required")
    hub = select_hub(conversation_id=conversation_id)
    addr = hub["address"]
    csrf = hub["csrf_token"]

    if wait_sec > 0:
        try:
            lsrpc_call(
                addr,
                csrf,
                "WaitForConversationFullyIdle",
                {
                    "conversationId": conversation_id,
                    "inactivityTimeoutSeconds": int(wait_sec),
                    "stabilizationDurationSeconds": 1,
                    "returnOnExecutorError": True,
                },
                timeout=float(wait_sec) + 5.0,
            )
        except Exception:
            pass

    traj = lsrpc_call(addr, csrf, "GetCascadeTrajectory", {"cascadeId": conversation_id}, timeout=10.0)
    digest = extract_trajectory_digest(traj)
    digest["conversation_id"] = conversation_id
    digest["hub_address"] = addr
    digest["artifacts"] = list_brain_artifacts(conversation_id)
    return digest


def tool_agy_read_transcript(
    conversation_id: str,
    start_step: int = 0,
    end_step: int = 50,
    include_thinking: bool = False,
    source: str = 'auto',
) -> dict[str, Any]:
    """Read trajectory steps from live Hub or local transcript.jsonl."""
    if not conversation_id:
        raise ValueError("conversation_id is required")
    transcript_file = _brain_root() / conversation_id / ".system_generated" / "logs" / "transcript.jsonl"
    if source not in {'auto','hub'}:
        raise ValueError('Unsupported transcript source')
    if source == 'auto' and transcript_file.is_file():
        lines = transcript_file.read_text(encoding="utf-8", errors="replace").splitlines()
        total = len(lines)
        s_idx = max(0, int(start_step))
        e_idx = min(total, int(end_step)) if end_step >= 0 else total
        steps_out: list[dict[str, Any]] = []
        for line in lines[s_idx:e_idx]:
            if not line.strip():
                continue
            obj = json.loads(line)
            if not include_thinking and "thinking" in obj:
                obj.pop("thinking", None)
            steps_out.append(obj)
        return {
            "source": "transcript.jsonl",
            "conversation_id": conversation_id,
            "total_steps": total,
            "start_step": s_idx,
            "end_step": e_idx,
            "steps": steps_out,
        }

    hub = select_hub(conversation_id=conversation_id)
    traj = lsrpc_call(
        hub["address"],
        hub["csrf_token"],
        "GetCascadeTrajectory",
        {"cascadeId": conversation_id},
        timeout=10.0,
    )
    raw_steps = (traj.get("trajectory") or {}).get("steps") or []
    total = len(raw_steps)
    s_idx = max(0, int(start_step))
    e_idx = min(total, int(end_step)) if end_step >= 0 else total
    steps_out = []
    for idx in range(s_idx, e_idx):
        st = raw_steps[idx]
        entry: dict[str, Any] = {
            "step_index": idx,
            "type": st.get("type"),
            "status": st.get("status"),
        }
        if st.get("type") == "CORTEX_STEP_TYPE_USER_INPUT":
            items = (st.get("userInput") or {}).get("items") or []
            entry["content"] = "\n".join(it.get("text", "") for it in items if it.get("text"))
        elif st.get("type") == "CORTEX_STEP_TYPE_PLANNER_RESPONSE":
            pr = st.get("plannerResponse") or {}
            entry["content"] = pr.get("modifiedResponse") or pr.get("response") or ""
            if include_thinking and pr.get("thinking"):
                entry["thinking"] = pr.get("thinking")
            if pr.get("toolCalls"):
                entry["tool_calls"] = pr.get("toolCalls")
        steps_out.append(entry)

    return {
        "source": "hub_rpc",
        "conversation_id": conversation_id,
        "total_steps": total,
        "start_step": s_idx,
        "end_step": e_idx,
        "steps": steps_out,
    }


def tool_agy_cancel_conversation(
    conversation_id: str,
    delete_trajectory: bool = False,
) -> dict[str, Any]:
    """Cancel an active Antigravity run and optionally delete the trajectory."""
    if not conversation_id:
        raise ValueError("conversation_id is required")
    hub = select_hub(conversation_id=conversation_id)
    addr = hub["address"]
    csrf = hub["csrf_token"]
    cancel_resp: dict[str, Any] = {}
    cancelled = False
    try:
        cancel_resp = lsrpc_call(
            addr,
            csrf,
            "CancelCascadeInvocation",
            {"cascadeId": conversation_id, "killBackgroundTasks": True},
            timeout=5.0,
        )
        cancelled = True
    except RuntimeError as exc:
        if "executor is not currently running" not in str(exc):
            raise
        cancel_resp = {"already_idle": True}
    deleted = False
    if delete_trajectory:
        lsrpc_call(addr, csrf, "DeleteCascadeTrajectory", {"cascadeId": conversation_id}, timeout=5.0)
        deleted = True
    return {
        "ok": True,
        "conversation_id": conversation_id,
        "cancelled": cancelled,
        "deleted": deleted,
        "cancel_response": cancel_resp,
    }


MCP_TOOLS: list[dict[str, Any]] = [
    {
        "name": "agy_hub_status",
        "description": (
            "Discover running VS Code Antigravity Hub instances (agy.exe --hub), "
            "active workspaces, conversation counts, and available Gemini models/quota."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "agy_list_conversations",
        "description": (
            "List recent Antigravity (Gemini) conversations in the VS Code panel, "
            "including conversation_id (cascadeId), title, run status, workspace, and artifacts."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "workspace": {
                    "type": "string",
                    "description": "Optional workspace folder path to filter conversations.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of conversations to return (default 20).",
                    "default": 20,
                },
            },
        },
    },
    {
        "name": "agy_dispatch_task",
        "description": (
            "Dispatch a task or follow-up prompt to Antigravity (Gemini). "
            "Defaults to mode='panel' which injects the task directly into the live VS Code "
            "Antigravity panel via LanguageServerService RPC."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": "Task instruction or handover prompt for Antigravity.",
                },
                "conversation_id": {
                    "type": "string",
                    "description": "Existing conversation_id (cascadeId) to continue; omit to create a new conversation.",
                },
                "workspace": {
                    "type": "string",
                    "description": "Target workspace directory path (defaults to current working directory).",
                },
                "project_id": {
                    "type": "string",
                    "description": "Optional Antigravity Project ID; auto-resolved from workspace via ListProjects/ReadProjects when omitted.",
                },
                "mode": {
                    "type": "string",
                    "enum": ["panel", "cli"],
                    "description": "Execution mode: 'panel' (default, live VS Code panel Hub RPC) or 'cli' (headless agy.exe).",
                    "default": "panel",
                },
                "focus_panel": {
                    "type": "boolean",
                    "description": "If true, triggers vscode:// URI to switch the foreground panel view to this conversation.",
                    "default": False,
                },
                "wait": {
                    "type": "boolean",
                    "description": "If true (default), waits until the turn finishes and returns the assistant response.",
                    "default": True,
                },
                "timeout_sec": {
                    "type": "integer",
                    "description": "Max seconds to wait when wait=true (default 300).",
                    "default": 300,
                },
                "model": {
                    "type": "string",
                    "description": "Optional model identifier override (default MODEL_PLACEHOLDER_M418).",
                    "default": DEFAULT_MODEL,
                },
                "skip_permissions": {
                    "type": "boolean",
                    "description": "In mode='cli', pass --dangerously-skip-permissions (default false).",
                    "default": False,
                },
                "strict_workspace": {
                    "type": "boolean",
                    "description": "If true, raise an error when no running Hub matches the target workspace.",
                    "default": False,
                },
            },
            "required": ["prompt"],
        },
    },
    {
        "name": "agy_get_conversation",
        "description": (
            "Query the live status, latest assistant response, recent tool calls, "
            "and artifact paths of an Antigravity conversation."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "conversation_id": {
                    "type": "string",
                    "description": "Target conversation_id (cascadeId).",
                },
                "wait_sec": {
                    "type": "integer",
                    "description": "Optional seconds to wait for the conversation to become idle before reading (default 0).",
                    "default": 0,
                },
            },
            "required": ["conversation_id"],
        },
    },
    {
        "name": "agy_read_transcript",
        "description": "Read step-by-step trajectory / transcript entries for an Antigravity conversation.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "conversation_id": {
                    "type": "string",
                    "description": "Target conversation_id (cascadeId).",
                },
                "start_step": {
                    "type": "integer",
                    "description": "Inclusive start step index (default 0).",
                    "default": 0,
                },
                "end_step": {
                    "type": "integer",
                    "description": "Exclusive end step index (default 50).",
                    "default": 50,
                },
                "include_thinking": {
                    "type": "boolean",
                    "description": "Include model thinking traces in output (default false).",
                    "default": False,
                },
            },
            "required": ["conversation_id"],
        },
    },
    {
        "name": "agy_cancel_conversation",
        "description": "Cancel a running Antigravity turn and optionally delete the conversation trajectory.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "conversation_id": {
                    "type": "string",
                    "description": "Target conversation_id (cascadeId).",
                },
                "delete_trajectory": {
                    "type": "boolean",
                    "description": "If true, also deletes the conversation trajectory from the Hub.",
                    "default": False,
                },
            },
            "required": ["conversation_id"],
        },
    },
]


MCP_TOOLS.extend(cli_job_schemas())
next(tool for tool in MCP_TOOLS if tool['name']=='agy_read_transcript')['inputSchema']['properties']['source'] = {
    'type':'string','enum':['auto','hub'],'description':'Use hub for stable native step indexes during exact-turn reconciliation.'}


def handle_mcp_request(msg: dict[str, Any]) -> dict[str, Any] | None:
    """Process a single JSON-RPC 2.0 request and return a JSON-RPC 2.0 response (or None for notifications)."""
    method = msg.get("method", "")
    req_id = msg.get("id")
    params = msg.get("params") or {}

    if req_id is None:
        return None

    try:
        if method == "initialize":
            client_proto = params.get("protocolVersion") or "2024-11-05"
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "protocolVersion": client_proto,
                    "capabilities": {
                        "tools": {"listChanged": False},
                    },
                    "serverInfo": {
                        "name": SERVER_NAME,
                        "version": SERVER_VERSION,
                    },
                },
            }
        if method == "ping":
            return {"jsonrpc": "2.0", "id": req_id, "result": {}}
        if method == "tools/list":
            return {"jsonrpc": "2.0", "id": req_id, "result": {"tools": MCP_TOOLS}}
        if method == "resources/list":
            return {"jsonrpc": "2.0", "id": req_id, "result": {"resources": []}}
        if method == "prompts/list":
            return {"jsonrpc": "2.0", "id": req_id, "result": {"prompts": []}}
        if method == "tools/call":
            t_name = params.get("name", "")
            t_args = params.get("arguments") or {}
            if t_name in {'agy_cli_task_start','agy_cli_task_status','agy_cli_task_result'}:
                res = call_cli_job(t_name,t_args)
            elif t_name == "agy_hub_status":
                res = tool_agy_hub_status()
            elif t_name == "agy_list_conversations":
                res = tool_agy_list_conversations(
                    workspace=t_args.get("workspace"),
                    limit=int(t_args.get("limit", 20)),
                )
            elif t_name == "agy_dispatch_task":
                res = tool_agy_dispatch_task(
                    prompt=t_args["prompt"],
                    conversation_id=t_args.get("conversation_id"),
                    workspace=t_args.get("workspace"),
                    project_id=t_args.get("project_id"),
                    mode=t_args.get("mode", "panel"),
                    focus_panel=bool(t_args.get("focus_panel", False)),
                    wait=bool(t_args.get("wait", True)),
                    timeout_sec=int(t_args.get("timeout_sec", 300)),
                    model=t_args.get("model", DEFAULT_MODEL),
                    skip_permissions=bool(t_args.get("skip_permissions", False)),
                    strict_workspace=bool(t_args.get("strict_workspace", False)),
                )
            elif t_name == "agy_get_conversation":
                res = tool_agy_get_conversation(
                    conversation_id=t_args["conversation_id"],
                    wait_sec=int(t_args.get("wait_sec", 0)),
                )
            elif t_name == "agy_read_transcript":
                res = tool_agy_read_transcript(
                    conversation_id=t_args["conversation_id"],
                    start_step=int(t_args.get("start_step", 0)),
                    end_step=int(t_args.get("end_step", 50)),
                    include_thinking=bool(t_args.get("include_thinking", False)),
                    source=t_args.get('source','auto'),
                )
            elif t_name == "agy_cancel_conversation":
                res = tool_agy_cancel_conversation(
                    conversation_id=t_args["conversation_id"],
                    delete_trajectory=bool(t_args.get("delete_trajectory", False)),
                )
            else:
                raise ValueError(f"Unknown tool: {t_name}")

            is_tool_err = bool(t_name == "agy_dispatch_task" and isinstance(res, dict) and res.get("ok") is False)
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps(res, ensure_ascii=False, indent=2),
                        }
                    ],
                    "isError": is_tool_err,
                },
            }

        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {"code": -32601, "message": f"Method not found: {method}"},
        }
    except Exception as exc:
        if isinstance(exc, JobError):
            error: dict[str, Any] = {"code": exc.code, "retryable": False}
            if getattr(exc, "message", None):
                error["message"] = exc.message
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "isError": True,
                    "structuredContent": error,
                    "content": [{"type": "text", "text": json.dumps(error, ensure_ascii=False)}],
                },
            }
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2),
                    }
                ],
                "isError": True,
            },
        }


def _write_stdio_response(stdout: Any, resp: dict[str, Any], use_content_length: bool) -> None:
    payload = json.dumps(resp, ensure_ascii=False).encode("utf-8")
    if use_content_length:
        header = f"Content-Length: {len(payload)}\r\n\r\n".encode("ascii")
        stdout.write(header + payload)
    else:
        stdout.write(payload + b"\n")
    stdout.flush()


def run_stdio_server() -> None:
    """Run the MCP server over stdin/stdout, supporting both NDJSON and Content-Length framing."""
    stdin = sys.stdin.buffer
    stdout = sys.stdout.buffer

    while True:
        first_line = stdin.readline()
        if not first_line:
            break
        stripped = first_line.strip()
        if not stripped:
            continue

        use_content_length = False
        is_header_block = b":" in stripped and not stripped.startswith(b"{") and not stripped.startswith(b"[")
        if is_header_block:
            use_content_length = True
            headers: dict[bytes, bytes] = {}
            k, v = stripped.split(b":", 1)
            headers[k.strip().lower()] = v.strip()
            while True:
                hdr = stdin.readline()
                if not hdr or hdr in (b"\r\n", b"\n"):
                    break
                if b":" in hdr:
                    hk, hv = hdr.split(b":", 1)
                    headers[hk.strip().lower()] = hv.strip()
            if b"content-length" not in headers:
                _write_stdio_response(
                    stdout,
                    {
                        "jsonrpc": "2.0",
                        "id": None,
                        "error": {"code": -32700, "message": "Parse error: missing Content-Length header"},
                    },
                    use_content_length=True,
                )
                continue
            try:
                length = int(headers[b"content-length"])
                body = stdin.read(length)
                msg = json.loads(body.decode("utf-8", errors="replace"))
            except Exception as exc:
                _write_stdio_response(
                    stdout,
                    {
                        "jsonrpc": "2.0",
                        "id": None,
                        "error": {"code": -32700, "message": f"Parse error: {exc}"},
                    },
                    use_content_length=True,
                )
                continue
        else:
            try:
                msg = json.loads(stripped.decode("utf-8", errors="replace"))
            except Exception as exc:
                _write_stdio_response(
                    stdout,
                    {
                        "jsonrpc": "2.0",
                        "id": None,
                        "error": {"code": -32700, "message": f"Parse error: {exc}"},
                    },
                    use_content_length=False,
                )
                continue

        resp = handle_mcp_request(msg)
        if resp is not None:
            _write_stdio_response(stdout, resp, use_content_length=use_content_length)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="subcommand")

    sub.add_parser("mcp", help="Run stdio MCP server (default when no subcommand is given)")
    sub.add_parser("status", help="Discover running Antigravity Hubs and models")

    p_list = sub.add_parser("list", help="List recent Antigravity conversations")
    p_list.add_argument("--workspace", help="Filter by workspace path")
    p_list.add_argument("--limit", type=int, default=20)

    p_disp = sub.add_parser("dispatch", help="Dispatch a task to Antigravity")
    p_disp.add_argument("prompt", help="Prompt text to send")
    p_disp.add_argument("--conversation", dest="conversation_id", help="Existing conversation ID")
    p_disp.add_argument("--workspace", help="Workspace path")
    p_disp.add_argument("--project", dest="project_id", help="Optional Antigravity Project ID override")
    p_disp.add_argument("--mode", choices=["panel", "cli"], default="panel")
    p_disp.add_argument("--model", default=DEFAULT_MODEL, help="Optional model override")
    p_disp.add_argument("--focus-panel", action="store_true")
    p_disp.add_argument("--no-wait", action="store_true")
    p_disp.add_argument("--timeout", type=int, default=300)
    p_disp.add_argument("--skip-permissions", action="store_true")
    p_disp.add_argument("--strict-workspace", action="store_true")

    p_get = sub.add_parser("get", help="Get conversation status and latest response")
    p_get.add_argument("conversation_id", help="Target conversation ID")
    p_get.add_argument("--wait-sec", type=int, default=0)

    p_read = sub.add_parser("read", help="Read conversation transcript steps")
    p_read.add_argument("conversation_id", help="Target conversation ID")
    p_read.add_argument("--start-step", type=int, default=0)
    p_read.add_argument("--end-step", type=int, default=50)
    p_read.add_argument("--include-thinking", action="store_true")

    p_cancel = sub.add_parser("cancel", help="Cancel a running conversation")
    p_cancel.add_argument("conversation_id", help="Target conversation ID")
    p_cancel.add_argument("--delete", action="store_true")

    args = parser.parse_args(argv)
    if args.subcommand in (None, "mcp"):
        run_stdio_server()
        return 0

    if args.subcommand == "status":
        out = tool_agy_hub_status()
    elif args.subcommand == "list":
        out = tool_agy_list_conversations(workspace=args.workspace, limit=args.limit)
    elif args.subcommand == "dispatch":
        out = tool_agy_dispatch_task(
            prompt=args.prompt,
            conversation_id=args.conversation_id,
            workspace=args.workspace,
            project_id=args.project_id,
            mode=args.mode,
            focus_panel=args.focus_panel,
            wait=not args.no_wait,
            timeout_sec=args.timeout,
            model=args.model,
            skip_permissions=args.skip_permissions,
            strict_workspace=args.strict_workspace,
        )
    elif args.subcommand == "get":
        out = tool_agy_get_conversation(args.conversation_id, wait_sec=args.wait_sec)
    elif args.subcommand == "read":
        out = tool_agy_read_transcript(
            args.conversation_id,
            start_step=args.start_step,
            end_step=args.end_step,
            include_thinking=args.include_thinking,
        )
    elif args.subcommand == "cancel":
        out = tool_agy_cancel_conversation(args.conversation_id, delete_trajectory=args.delete)
    else:
        parser.error(f"Unknown subcommand: {args.subcommand}")
        return 2

    print(json.dumps(out, ensure_ascii=False, indent=2))
    if args.subcommand == "dispatch" and isinstance(out, dict) and out.get("ok") is False:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
