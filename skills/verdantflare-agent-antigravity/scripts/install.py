#!/usr/bin/env python3
"""Install/update verdantflare-agent-antigravity to ~/.agents/skills and optionally register MCP in peer hosts."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import shutil
import sys
import tempfile
import tomllib

DEFAULT_ROOTS = ("~/.agents/skills",)
MCP_SERVER_KEY = "verdantflare-agent-antigravity"
LEGACY_MCP_KEYS = ("antigravity",)


def fingerprint(root: Path) -> dict[str, str]:
    return {
        p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in root.rglob("*")
        if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc"
    }


def install(destination: str | Path, replace: bool = False) -> Path:
    source = Path(__file__).resolve().parents[1]
    dest_root = Path(destination).expanduser().resolve()
    target = dest_root / source.name
    if target == source or target.is_relative_to(source) or source.is_relative_to(target):
        raise ValueError("Install target must be separate from development source")
    dest_root.mkdir(parents=True, exist_ok=True)
    expected = fingerprint(source)
    if target.exists():
        if fingerprint(target) == expected:
            print(f"Already current: {target}")
            return target
        if not replace:
            raise RuntimeError(
                f"Existing install differs at {target}; re-run with --replace after verifying no local-only edits."
            )
    stage = Path(tempfile.mkdtemp(prefix=".agy-skill-install-", dir=dest_root.parent))
    backup = None
    try:
        shutil.copytree(source, stage / source.name, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        if fingerprint(stage / source.name) != expected:
            raise ValueError("Installation copy mismatch")
        if target.exists():
            backup_root = dest_root.parent / "skill-backups"
            backup_root.mkdir(exist_ok=True)
            backup = backup_root / (
                source.name + "-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            )
            target.rename(backup)
        try:
            (stage / source.name).rename(target)
        except Exception:
            if backup:
                backup.rename(target)
            raise
        print(f"Installed and hash-verified: {target}")
        if backup:
            print(f"Previous install retained: {backup}")
        return target
    finally:
        shutil.rmtree(stage, ignore_errors=True)


def register_claude_mcp(server_script: Path, claude_config_path: Path | None = None) -> Path:
    """Register or update mcpServers.verdantflare-agent-antigravity in ~/.claude.json and remove legacy keys."""
    cfg_path = (claude_config_path or Path("~/.claude.json")).expanduser().resolve()
    data: dict = {}
    if cfg_path.is_file():
        data = json.loads(cfg_path.read_text(encoding="utf-8"))
    mcp_servers = data.setdefault("mcpServers", {})
    for legacy_key in LEGACY_MCP_KEYS:
        mcp_servers.pop(legacy_key, None)
    mcp_servers[MCP_SERVER_KEY] = {
        "command": "python",
        "args": ["-X", "utf8", server_script.as_posix(), "mcp"],
    }
    cfg_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Registered MCP '{MCP_SERVER_KEY}' in Claude Code config: {cfg_path}")
    return cfg_path


def _strip_toml_mcp_sections(text: str, keys: tuple[str, ...]) -> str:
    cleaned = text
    for key in keys:
        pattern = re.compile(
            rf"(?ms)^\[mcp_servers\.(?:{re.escape(key)}|\"{re.escape(key)}\")(?:\.[^\]]+)?\]\r?\n.*?(?=^\[|\Z)"
        )
        cleaned = pattern.sub("", cleaned)
    return cleaned.rstrip()


def register_codex_mcp(server_script: Path, codex_config_path: Path | None = None) -> Path:
    """Register or update [mcp_servers.verdantflare-agent-antigravity] in ~/.codex/config.toml."""
    cfg_path = (codex_config_path or Path("~/.codex/config.toml")).expanduser().resolve()
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    text = cfg_path.read_text(encoding="utf-8") if cfg_path.is_file() else ""
    script_posix = server_script.as_posix()
    block = (
        f"[mcp_servers.{MCP_SERVER_KEY}]\n"
        f'command = "python"\n'
        f'args = [ "-X", "utf8", "{script_posix}", "mcp" ]\n'
        f"startup_timeout_sec = 30\n"
        f"tool_timeout_sec = 450\n"
    )
    cleaned = _strip_toml_mcp_sections(text, (MCP_SERVER_KEY, *LEGACY_MCP_KEYS))
    new_text = (cleaned + "\n\n" + block) if cleaned else block
    tomllib.loads(new_text)
    cfg_path.write_text(new_text, encoding="utf-8")
    print(f"Registered MCP '{MCP_SERVER_KEY}' in Codex config: {cfg_path}")
    return cfg_path


def register_grok_mcp(server_script: Path, grok_config_path: Path | None = None) -> Path:
    """Register or update [mcp_servers.verdantflare-agent-antigravity] in ~/.grok/config.toml."""
    cfg_path = (grok_config_path or Path("~/.grok/config.toml")).expanduser().resolve()
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    text = cfg_path.read_text(encoding="utf-8") if cfg_path.is_file() else ""
    script_posix = server_script.as_posix()
    block = (
        f"[mcp_servers.{MCP_SERVER_KEY}]\n"
        f'command = "python"\n'
        f'args = [ "-X", "utf8", "{script_posix}", "mcp" ]\n'
        f"enabled = true\n"
    )
    cleaned = _strip_toml_mcp_sections(text, (MCP_SERVER_KEY, *LEGACY_MCP_KEYS))
    new_text = (cleaned + "\n\n" + block) if cleaned else block
    tomllib.loads(new_text)
    cfg_path.write_text(new_text, encoding="utf-8")
    print(f"Registered MCP '{MCP_SERVER_KEY}' in Grok config: {cfg_path}")
    return cfg_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--skills-root",
        action="append",
        help=f"Global skills root; repeatable. Default: {', '.join(DEFAULT_ROOTS)}",
    )
    parser.add_argument(
        "--replace",
        action="store_true",
        help="Replace an existing installed copy when file hashes differ.",
    )
    parser.add_argument(
        "--register-mcp",
        action="store_true",
        help="Also register the MCP server in peer hosts (Claude Code, Codex, Grok).",
    )
    args = parser.parse_args(argv)
    roots = args.skills_root or list(DEFAULT_ROOTS)
    installed_target: Path | None = None
    for root in roots:
        installed_target = install(root, replace=args.replace)

    if args.register_mcp and installed_target is not None:
        server_script = installed_target / "scripts" / "agy_mcp_server.py"
        home = Path.home()
        if (home / ".claude.json").exists() or (home / ".claude").exists():
            register_claude_mcp(server_script)
        if (home / ".codex").exists():
            register_codex_mcp(server_script)
        if (home / ".grok").exists():
            register_grok_mcp(server_script)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
