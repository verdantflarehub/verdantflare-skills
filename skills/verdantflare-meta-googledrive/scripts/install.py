"""Install/update the skill from its repository source; retain previous install."""
import argparse
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import shutil
import tempfile

# Unified install root; each agent host is wired to read it once (see verdantflare-meta-skill install guide).
DEFAULT_ROOTS = ("~/.agents/skills",)


def fingerprint(root):
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob("*") if p.is_file() and "__pycache__" not in p.parts
            and p.suffix != ".pyc"}


def install(destination):
    source = Path(__file__).resolve().parents[1]
    destination = Path(destination).expanduser().resolve()
    target = destination / source.name
    if target == source or target.is_relative_to(source) or source.is_relative_to(target):
        raise ValueError("Install target must be separate from development source")
    destination.mkdir(parents=True, exist_ok=True)
    expected = fingerprint(source)
    if target.exists() and fingerprint(target) == expected:
        print(f"Already current: {target}")
        return target
    stage = Path(tempfile.mkdtemp(prefix=".gdrive-install-", dir=destination.parent))
    backup = None
    try:
        shutil.copytree(source, stage / source.name, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        if fingerprint(stage / source.name) != expected:
            raise ValueError("Installation copy mismatch")
        if target.exists():
            backup_root = destination.parent / "skill-backups"
            backup_root.mkdir(exist_ok=True)
            backup = backup_root / (source.name + "-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ"))
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
        shutil.rmtree(stage)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skills-root", action="append",
                        help=f"Global skills root; repeatable. Default: {', '.join(DEFAULT_ROOTS)}")
    for root in parser.parse_args().skills_root or DEFAULT_ROOTS:
        install(root)
