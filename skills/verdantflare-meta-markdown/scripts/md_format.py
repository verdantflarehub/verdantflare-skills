"""Format or check explicit Markdown files with an auto-discovered Prettier.

Exit codes: 0 ok, 1 check found unformatted files or Prettier failed, 2 Prettier not found.
"""
import argparse
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

DEFAULT_FLAGS = ["--prose-wrap", "preserve", "--embedded-language-formatting", "off", "--end-of-line", "lf"]
EXTENSION_ROOTS = [".vscode", ".vscode-insiders", ".cursor", ".antigravity", ".windsurf", ".vscode-oss"]


def _version_key(path):
    pkg = Path(path).parents[1] / "package.json"
    try:
        match = re.search(r'"version"\s*:\s*"(\d+)\.(\d+)\.(\d+)', pkg.read_text(encoding="utf-8"))
        return tuple(int(x) for x in match.groups()) if match else (0, 0, 0)
    except OSError:
        return (0, 0, 0)


def _command_for(path):
    path = str(path)
    if path.endswith((".cjs", ".js", ".mjs")):
        node = shutil.which("node")
        return [node, path] if node else None
    return [path]


def discover(explicit=None, start=None, home=None):
    """Return (command list, source label) or (None, reason)."""
    explicit = explicit or os.environ.get("VERDANTFLARE_PRETTIER")
    if explicit:
        if Path(explicit).exists():
            cmd = _command_for(explicit)
            return (cmd, "explicit") if cmd else (None, "node not found for " + explicit)
        return None, "explicit Prettier not found: " + explicit
    if start:
        for parent in [Path(start).resolve(), *Path(start).resolve().parents]:
            for name in ("prettier.cmd", "prettier"):
                candidate = parent / "node_modules" / ".bin" / name
                if candidate.exists():
                    return [str(candidate)], "repository"
    on_path = shutil.which("prettier")
    if on_path:
        return [on_path], "PATH"
    home = Path(home) if home else Path.home()
    found = []
    for root in EXTENSION_ROOTS:
        found += (home / root / "extensions").glob("esbenp.prettier-vscode-*/node_modules/prettier/bin/prettier.cjs")
    if found:
        best = max(found, key=_version_key)
        cmd = _command_for(best)
        if cmd:
            return cmd, "editor extension"
        return None, "node not found for editor-bundled Prettier"
    return None, "no Prettier found (repository, PATH, editor extensions)"


def has_config(cmd, file):
    result = subprocess.run(cmd + ["--find-config-path", str(file)], capture_output=True, text=True,
                            encoding="utf-8", errors="replace")
    return result.returncode == 0 and bool(result.stdout.strip())


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("files", nargs="+", help="explicit Markdown files; no globs over the whole repository")
    parser.add_argument("--check", action="store_true", help="check only, do not write")
    parser.add_argument("--prettier", help="Prettier executable or prettier.cjs path")
    args = parser.parse_args(argv)

    files = [Path(f) for f in args.files]
    missing = [str(f) for f in files if not f.is_file()]
    if missing:
        print("Missing files: " + ", ".join(missing), file=sys.stderr)
        return 1
    cmd, source = discover(args.prettier, files[0].parent)
    if not cmd:
        print("Format 未完成: " + source, file=sys.stderr)
        return 2
    version = subprocess.run(cmd + ["--version"], capture_output=True, text=True).stdout.strip()
    print(f"Prettier {version} ({source}): {' '.join(cmd)}")

    status = 0
    for file in files:
        flags = [] if has_config(cmd, file) else DEFAULT_FLAGS
        mode = "--check" if args.check else "--write"
        # Prettier 3 skips .gitignore'd files silently; explicit files honor only .prettierignore.
        result = subprocess.run(cmd + [mode, "--ignore-path", ".prettierignore", *flags, str(file)], capture_output=True, text=True,
                                encoding="utf-8", errors="replace")
        output = (result.stdout + result.stderr).strip()
        if output:
            print(output)
        if result.returncode != 0:
            status = 1
    return status


if __name__ == "__main__":
    sys.exit(main())
