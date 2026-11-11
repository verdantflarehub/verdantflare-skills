#!/usr/bin/env python3
"""Self-contained installer for verdantflare-agent-codex skill, isolated MCP runtime, and peer host registration."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import tomllib
import venv

SKILL_NAME = "verdantflare-agent-codex"
PACKAGE_NAME = "verdantflare_agent_codex"
MCP_KEY = "verdantflare-agent-codex"
DEFAULT_SKILLS_ROOT = "~/.agents/skills"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fingerprint_skill(root: Path) -> dict[str, str]:
    return {
        p.relative_to(root).as_posix(): digest(p)
        for p in sorted(root.rglob("*"))
        if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc"
    }


def fingerprint_service(root: Path) -> dict[str, str]:
    return {
        p.relative_to(root).as_posix(): digest(p)
        for p in sorted(root.rglob("*"))
        if p.is_file() and not any(part in ("__pycache__", "tests") for part in p.relative_to(root).parts) and p.suffix != ".pyc"
    }


def sync_skill_tree(skill_source: Path, skill_target: Path, replace: bool) -> dict[str, str]:
    skill_source = skill_source.resolve()
    skill_target = skill_target.expanduser().resolve()
    if skill_target == skill_source or skill_target.is_relative_to(skill_source) or skill_source.is_relative_to(skill_target):
        raise ValueError("Skill install target must be separate from development source")
    skill_target.parent.mkdir(parents=True, exist_ok=True)
    expected = fingerprint_skill(skill_source)
    if skill_target.exists():
        if fingerprint_skill(skill_target) == expected:
            return expected
        if not replace:
            raise ValueError(f"Existing skill copy differs at {skill_target}; re-run with --replace")
    stage_parent = skill_target.parent.parent if skill_target.parent.parent.exists() else skill_target.parent
    stage = Path(tempfile.mkdtemp(prefix=f".{SKILL_NAME}-install-", dir=stage_parent))
    backup = None
    try:
        staged = stage / SKILL_NAME
        shutil.copytree(skill_source, staged, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        if fingerprint_skill(staged) != expected:
            raise ValueError("Staged skill copy hash mismatch")
        if skill_target.exists():
            backup_root = stage_parent / "skill-backups"
            backup_root.mkdir(exist_ok=True)
            backup = backup_root / f"{SKILL_NAME}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')}"
            skill_target.rename(backup)
        try:
            staged.rename(skill_target)
        except Exception:
            if backup and backup.exists():
                backup.rename(skill_target)
            raise
        return expected
    finally:
        shutil.rmtree(stage, ignore_errors=True)


def sync_service_tree(service_source: Path, destination: Path, replace: bool, previous_hashes: dict[str, str] | None) -> dict[str, str]:
    service_source = service_source.resolve()
    destination = destination.resolve()
    expected = fingerprint_service(service_source)
    if destination.exists():
        current = fingerprint_service(destination)
        if current == expected:
            return expected
        if previous_hashes is not None and not replace:
            for rel, old_hash in previous_hashes.items():
                inst = destination / rel
                if inst.exists() and digest(inst) != old_hash:
                    raise ValueError(f"Installed runtime file edited ({rel}); pass --replace to overwrite")
        elif previous_hashes is None and not replace:
            raise ValueError(f"Untracked runtime files exist at {destination}; pass --replace to overwrite")
        shutil.rmtree(destination)
    destination.mkdir(parents=True, exist_ok=True)
    for rel in expected:
        src_file = service_source / rel
        dst_file = destination / rel
        dst_file.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src_file, dst_file)
    if fingerprint_service(destination) != expected:
        raise ValueError("Service runtime installation hash mismatch")
    return expected


def _read_vscode_obsolete(ext_root: Path) -> dict:
    obs = ext_root / ".obsolete"
    if obs.is_file():
        try:
            data = json.loads(obs.read_text("utf-8"))
            if isinstance(data, dict):
                return data
        except Exception:
            pass
    return {}


def auto_find_codex_extension() -> Path:
    ext_root = Path.home() / ".vscode" / "extensions"
    obsolete = _read_vscode_obsolete(ext_root)
    dirs = [d for d in ext_root.glob("openai.chatgpt-*") if d.is_dir() and (d / "package.json").is_file()]
    active = [d for d in dirs if not obsolete.get(d.name)] or dirs
    candidates = sorted(active, key=lambda p: p.stat().st_mtime, reverse=True)
    if not candidates:
        raise FileNotFoundError("Could not locate openai.chatgpt-* under ~/.vscode/extensions")
    return candidates[0].resolve()


def auto_find_codex_binary(extension_dir: Path) -> Path:
    exe_name = "codex.exe" if os.name == "nt" else "codex"
    direct = extension_dir / "bin" / "windows-x86_64" / exe_name
    if direct.is_file():
        return direct.resolve()
    found = sorted(extension_dir.rglob(exe_name))
    if found:
        return found[0].resolve()
    which_res = shutil.which("codex")
    if which_res:
        return Path(which_res).resolve()
    raise FileNotFoundError(f"Could not locate {exe_name} in {extension_dir} or PATH")


def auto_find_node_binary() -> Path:
    which_res = shutil.which("node")
    if which_res:
        return Path(which_res).resolve()
    raise FileNotFoundError("Could not locate node executable in PATH")


def upsert_json_mcp(cfg_path: Path, key: str, launch: dict) -> str:
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    data = json.loads(cfg_path.read_text("utf-8")) if cfg_path.is_file() else {}
    data.setdefault("mcpServers", {})[key] = launch
    cfg_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", "utf-8")
    return str(cfg_path)


def upsert_toml_mcp(cfg_path: Path, key: str, launch: dict, include_enabled: bool = False) -> str:
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    text = cfg_path.read_text("utf-8") if cfg_path.is_file() else ""
    pattern = re.compile(rf"(?ms)^\[mcp_servers\.{re.escape(key)}(?:\.[^\]]+)?\]\r?\n.*?(?=^\[|\Z)")
    cleaned = pattern.sub("", text).rstrip()
    lines = [
        f"[mcp_servers.{key}]",
        f"command = {json.dumps(launch['command'])}",
        f"args = [{', '.join(json.dumps(a) for a in launch['args'])}]",
    ]
    if include_enabled:
        lines.append("enabled = true")
    env = launch.get("env") or {}
    if env:
        lines.append(f"\n[mcp_servers.{key}.env]")
        for k, v in env.items():
            lines.append(f"{k} = {json.dumps(str(v))}")
    block = "\n".join(lines) + "\n"
    new_text = (cleaned + "\n\n" + block) if cleaned else block
    tomllib.loads(new_text)
    cfg_path.write_text(new_text, "utf-8")
    return str(cfg_path)


def register_peer_hosts(launch: dict) -> list[str]:
    registered: list[str] = []
    home = Path.home()
    if (home / ".gemini").exists():
        registered.append(upsert_json_mcp(home / ".gemini/config/mcp_config.json", MCP_KEY, launch))
    if (home / ".claude.json").exists() or (home / ".claude").exists():
        registered.append(upsert_json_mcp(home / ".claude.json", MCP_KEY, launch))
    if (home / ".grok").exists():
        registered.append(upsert_toml_mcp(home / ".grok/config.toml", MCP_KEY, launch, include_enabled=True))
    return registered


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--codex", default=None, help="Path to codex binary (auto-detected if omitted).")
    parser.add_argument("--node", default=None, help="Path to node binary (auto-detected if omitted).")
    parser.add_argument("--extension", default=None, help="Path to openai.chatgpt-* extension directory.")
    parser.add_argument("--allow-root", action="append", default=None, help='Optional workspace root restriction; omit or "*" for all.')
    parser.add_argument("--replace", "--replace-config", dest="replace", action="store_true", help="Replace existing skill/runtime copies or config when hashes differ.")
    parser.add_argument("--register-mcp", action="store_true", help="Idempotently register verdantflare-agent-codex in peer hosts (Antigravity, Claude Code, Grok).")
    parser.add_argument("--target", type=Path, default=Path(os.environ.get("LOCALAPPDATA", Path.home() / ".local/share")) / SKILL_NAME)
    parser.add_argument("--skills-root", action="append", default=None, help=f"Global skills root (default: {DEFAULT_SKILLS_ROOT}).")
    parser.add_argument("--skill-target", type=Path, default=None, help="Explicit skill destination override.")
    args = parser.parse_args(argv)

    script_dir = Path(__file__).resolve().parent
    skill_source = script_dir.parent
    service_source = script_dir / PACKAGE_NAME
    if not (service_source / "__main__.py").is_file():
        raise FileNotFoundError(f"Bundled service package missing at {service_source}")

    extension_dir = Path(args.extension).resolve() if args.extension else auto_find_codex_extension()
    codex_bin = Path(args.codex).resolve() if args.codex else auto_find_codex_binary(extension_dir)
    node_bin = Path(args.node).resolve() if args.node else auto_find_node_binary()

    skill_targets = [args.skill_target.expanduser().resolve()] if args.skill_target else [
        Path(root).expanduser().resolve() / SKILL_NAME for root in (args.skills_root or [DEFAULT_SKILLS_ROOT])
    ]

    target = args.target.expanduser().resolve()
    target.mkdir(parents=True, exist_ok=True)
    manifest_path = target / "install-manifest.json"
    previous_service = None
    if manifest_path.is_file():
        try:
            previous_service = json.loads(manifest_path.read_text("utf-8")).get("service")
        except Exception:
            previous_service = None

    environment = target / "venv"
    if not environment.exists():
        venv.EnvBuilder(with_pip=True).create(environment)
    python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    subprocess.run([str(python), "-m", "pip", "install", "--quiet", "-r", str(service_source / "requirements.lock.txt")], check=True)

    service_hashes = sync_service_tree(service_source, target / "app" / PACKAGE_NAME, replace=args.replace, previous_hashes=previous_service)
    skill_hashes: dict[str, str] = {}
    for st in skill_targets:
        skill_hashes = sync_skill_tree(skill_source, st, replace=args.replace)

    raw_roots = args.allow_root or ["*"]
    allowed_roots = ["*"] if "*" in raw_roots else [str(Path(p).resolve(strict=True)) for p in raw_roots]
    config = {
        "database": str(target / "state/tasks.sqlite3"),
        "codex": str(codex_bin),
        "node": str(node_bin),
        "extension": str(extension_dir),
        "allowed_roots": allowed_roots,
    }
    config_path = target / "config.json"
    if config_path.exists() and json.loads(config_path.read_text("utf-8")) != config and not args.replace:
        raise ValueError("Existing runtime configuration differs; re-run with --replace to apply updated config")
    config_path.write_text(json.dumps(config, indent=2) + "\n", "utf-8")

    launch = {
        "command": str(python),
        "args": ["-X", "utf8", "-m", PACKAGE_NAME, "--config", str(config_path)],
        "env": {"PYTHONPATH": str(target / "app"), "PYTHONUTF8": "1"},
    }
    (target / "mcp-registration.json").write_text(json.dumps({"mcpServers": {MCP_KEY: launch}}, indent=2) + "\n", "utf-8")
    (target / "service.json").write_text(json.dumps(launch, indent=2) + "\n", "utf-8")
    manifest_path.write_text(json.dumps({"service": service_hashes, "skill": skill_hashes}, indent=2) + "\n", "utf-8")

    registered = register_peer_hosts(launch) if args.register_mcp else []
    print(
        json.dumps(
            {
                "runtime": str(target),
                "skill": str(skill_targets[-1]),
                "service_files": len(service_hashes),
                "skill_files": len(skill_hashes),
                "host_registration": registered if args.register_mcp else "prepared-not-applied",
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
