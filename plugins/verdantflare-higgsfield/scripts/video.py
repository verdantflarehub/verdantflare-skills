"""Delegate the adapted official workflow to the user's canonical Video Skill."""
import os
from pathlib import Path
import subprocess
import sys


def resolve():
    configured = os.getenv("VERDANTFLARE_SKILLS_ROOT")
    candidates = [Path(configured)] if configured else list(Path(__file__).resolve().parents)
    if not configured:
        for workspace in (Path.cwd(), *Path.cwd().parents):
            candidates.extend((workspace, workspace / "verdantflare-skills"))
    for root in candidates:
        client = root / "skills/verdantflare-video/scripts/video_client.py"
        skill = root / "skills/verdantflare-video/SKILL.md"
        if client.is_file() and skill.is_file():
            return client
    raise ValueError("Set VERDANTFLARE_SKILLS_ROOT to the installed verdantflare-skills repository")


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
