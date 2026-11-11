"""Install/update this skill into the unified skills root (default ~/.agents/skills).

Follows verdantflare-meta-skill install rules: copy the whole maintained source, exclude caches, verify file-by-file.
An existing install that differs from the source is only replaced with --replace, after the caller has
confirmed it holds no edits that exist only in the installed copy.
"""
import argparse
import hashlib
from pathlib import Path
import shutil
import sys

IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc")


def fingerprint(root):
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob("*") if p.is_file() and "__pycache__" not in p.parts
            and p.suffix != ".pyc"}


def diff(source, target):
    a, b = fingerprint(source), fingerprint(target)
    return sorted(k for k in a.keys() | b.keys() if a.get(k) != b.get(k))


def install(destination, replace=False):
    source = Path(__file__).resolve().parents[1]
    destination = Path(destination).expanduser().resolve()
    target = destination / source.name
    if target == source or target.is_relative_to(source) or source.is_relative_to(target):
        raise ValueError("Install target must be separate from development source")
    if target.exists():
        changed = diff(source, target)
        if not changed:
            print(f"Already current: {target}")
            return target
        if not replace:
            print("Installed copy differs from source:\n  " + "\n  ".join(changed))
            raise SystemExit("Confirm no install-only edits exist (merge them back to source), then rerun with --replace")
        shutil.rmtree(target)
    destination.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, target, ignore=IGNORE)
    if diff(source, target):
        raise ValueError("Installation copy mismatch")
    print(f"Installed and hash-verified: {target}")
    return target


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skills-root", default=str(Path.home() / ".agents" / "skills"))
    parser.add_argument("--replace", action="store_true", help="replace a differing installed copy")
    args = parser.parse_args()
    install(args.skills_root, args.replace)
    sys.exit(0)
