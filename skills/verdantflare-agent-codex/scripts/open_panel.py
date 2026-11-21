"""Open a configured project in its dedicated VS Code dispatch window."""
import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import subprocess
import uuid

from verdantflare_agent_codex.panel import current_window_mailbox, open_panel_tab, validate_panel_config
from verdantflare_agent_codex.plugin import resolve_codex_binary, resolve_codex_extension
from verdantflare_agent_codex.store import allowed_cwd, canonical


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project", type=Path)
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--session", type=uuid.UUID, help="Open an existing session in the calling Codex window")
    target.add_argument("--code", type=Path, help="Path to Code.exe for a dedicated dispatch window")
    parser.add_argument("--config", type=Path, default=Path(os.environ.get("LOCALAPPDATA", ".")) /
                        "verdantflare-agent-codex" / "config.json")
    args = parser.parse_args()
    if args.session:
        workspace = str(args.project.resolve(strict=True))
        receipt = asyncio.run(open_panel_tab(current_window_mailbox(), workspace, str(args.session)))
        print(json.dumps({"thread_id": receipt["thread_id"], "opened": receipt["opened"]}))
        return
    config_path = args.config.resolve(strict=True)
    config = json.loads(config_path.read_text("utf-8"))
    workspace = allowed_cwd(str(args.project.resolve(strict=True)), config["allowed_roots"])
    panel = config.get("panel_transport")
    validate_panel_config(panel)
    hosts = [host for host in panel["hosts"] if canonical(host["cwd"]) == workspace]
    if len(hosts) != 1:
        parser.error("The project requires one configured window endpoint")
    socket = Path(hosts[0]["socket"])
    if not socket.parent.is_dir():
        parser.error("The configured private socket directory is missing")
    transport = str(Path(panel["client"]).resolve(strict=True))
    extension = resolve_codex_extension(config["extension"])
    upstream = Path(resolve_codex_binary(config["codex"], extension)).resolve(strict=True)
    code = args.code.resolve(strict=True)
    window_extension = Path(__file__).resolve().parents[1] / "resources" / "panel-window"
    profile_key = hashlib.sha256(workspace.encode("utf-8")).hexdigest()[:20]
    profile = config_path.parent / "windows" / profile_key
    settings_file = profile / "User" / "settings.json"
    settings_file.parent.mkdir(parents=True, exist_ok=True)
    if settings_file.exists():
        settings = json.loads(settings_file.read_text("utf-8-sig"))
        if settings.get("chatgpt.cliExecutable") != transport:
            parser.error("The dispatch profile is configured for a different CLI executable")
    else:
        with settings_file.open("x", encoding="utf-8") as output:
            json.dump({"chatgpt.cliExecutable": transport, "chatgpt.openOnStartup": True}, output)
    environment = dict(os.environ)
    environment.pop("ELECTRON_RUN_AS_NODE", None)
    environment.update(VERDANTFLARE_CODEX_PANEL_SOCKET=str(socket), VERDANTFLARE_CODEX_PANEL_CLI=str(upstream))
    process = subprocess.Popen([
        str(code), "--user-data-dir", str(profile), "--extensions-dir", str(profile / "extensions"),
        f"--extensionDevelopmentPath={extension}", f"--extensionDevelopmentPath={window_extension}",
        "--skip-welcome", "--skip-release-notes", workspace,
    ], env=environment, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print(json.dumps({"launcher_pid": process.pid}))


if __name__ == "__main__":
    main()
