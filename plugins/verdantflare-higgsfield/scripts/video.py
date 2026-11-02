"""Delegate the adapted official workflow to the user's canonical Video Skill."""
import os
import ast
from pathlib import Path
import subprocess
import sys


def resolve():
    configured = os.getenv("VERDANTFLARE_SKILLS_ROOT")
    candidates = [Path(configured)] if configured else list(Path(__file__).resolve().parents)
    if not configured:
        # A standalone Video Skill is sufficient; a developer checkout is optional.
        candidates.append(Path(os.getenv("CODEX_HOME", Path.home() / ".codex")))
        candidates.append(Path.home() / ".agents")
        for workspace in (Path.cwd(), *Path.cwd().parents):
            candidates.extend((workspace, workspace / "verdantflare-skills", workspace / ".agents", workspace / ".codex"))
    for root in candidates:
        client = root / "skills/verdantflare-video/scripts/video_client.py"
        skill = root / "skills/verdantflare-video/SKILL.md"
        if client.is_file() and skill.is_file():
            try:
                tree = ast.parse(client.read_text(encoding="utf-8-sig"))
            except (OSError, UnicodeError, SyntaxError):
                continue
            if any(isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant)
                   and node.value.value == "channel-capabilities-v1"
                   and any(isinstance(target, ast.Name) and target.id == "STUDIO_VIDEO_CLIENT_CONTRACT" for target in node.targets)
                   for node in tree.body):
                return client
    raise ValueError("Install the Studio MCP edition of verdantflare-video v0.5.0 or newer, or set VERDANTFLARE_SKILLS_ROOT to it; the public API edition is incompatible")


def arguments(args):
    if not args or args[0] not in {"check", "generate", "status", "result", "resume", "import", "--help", "-h"}:
        raise ValueError("Use check, generate, status, result, resume or import")
    if args[0] == "generate":
        if any(arg == "--route" or arg.startswith("--route=") for arg in args):
            raise ValueError("This plugin fixes route=higgsfield; use verdantflare-video for other routes")
        return [*args, "--route", "higgsfield"]
    return args


def main():
    try:
        args = arguments(sys.argv[1:])
        return subprocess.run([sys.executable, str(resolve()), *args], shell=False).returncode
    except (ValueError, OSError) as error:
        print(str(error) if isinstance(error, ValueError) else "Video Skill unavailable", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
