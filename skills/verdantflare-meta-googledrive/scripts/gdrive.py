"""Project asset storage through an existing Google Drive Desktop mount.

project/.gdrive links to <drive-root>/<namespace>; the Drive client does the syncing.
Files are immutable by key. No API credentials, no local history, no mirror delete.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tempfile
import time
from urllib.parse import unquote, urlsplit
from html.parser import HTMLParser

from markdown_it import MarkdownIt

BUCKETS = ("approved", "pending", "references")
SKIP = {".git", ".venv", "venv", "node_modules", "__pycache__"}
REVISION = re.compile(r"--([0-9a-f]{64})$")
LONG_PATH = 240
NON_ASSETS = {".md", ".markdown", ".py", ".js", ".ts", ".json", ".yaml", ".yml", ".toml", ".txt", ".html", ".css"}


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def relative(value):
    value = str(value).replace("\\", "/")
    parts = PurePosixPath(value).parts
    if not parts or value.startswith("/") or any(
        p in {".", "..", ".gdrive"} or re.search(r'[<>:"|?*\x00-\x1f]', p)
        or p.endswith((".", " "))
        or re.fullmatch(r"(?i)(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?", p)
        for p in parts
    ):
        raise ValueError("Expected a safe relative path without .gdrive or parent traversal")
    return Path(*parts)


def inside(root, key):
    root = Path(root).resolve()
    path = root / relative(key)
    if not path.resolve().is_relative_to(root):
        raise ValueError("Path escapes storage root")
    # Reject junctions/symlinks even when they point back into the root.
    for p in [path, *path.parents]:
        if p == root:
            break
        if p.is_symlink() or (hasattr(p, "is_junction") and p.is_junction()):
            raise ValueError("Linked storage paths are not supported")
    return path


def emit(data):
    print(json.dumps(data, ensure_ascii=False, indent=2))


def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", newline="\n", dir=path.parent, delete=False) as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        name = f.name
    os.replace(name, path)


def copy_verified(source, target, expected=None):
    """Never overwrite a different existing object; verify bytes before publication."""
    expected = expected or digest(source)
    if target.exists():
        if not target.is_file() or digest(target) != expected:
            raise ValueError(f"Content conflict: {target}")
        return "unchanged"
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=target.parent, prefix=".transfer-", delete=False) as f:
        staging = Path(f.name)
    try:
        shutil.copyfile(source, staging)
        if digest(staging) != expected:
            raise ValueError("Copy verification failed; source may have changed")
        # Exclusive create: another local writer cannot silently replace this object.
        try:
            with target.open("xb") as out, staging.open("rb") as inp:
                shutil.copyfileobj(inp, out)
        except FileExistsError:
            if digest(target) != expected:
                raise ValueError(f"Concurrent content conflict: {target}")
        if digest(target) != expected:
            raise ValueError(f"Incomplete destination; inspect and retry: {target}")
    finally:
        staging.unlink(missing_ok=True)
    return "copied"


class MediaHTML(HTMLParser):
    def __init__(self):
        super().__init__()
        self.urls = []

    def handle_starttag(self, tag, attrs):
        for key, value in attrs:
            if value and key in {"src", "href", "poster"}:
                self.urls.append(value)
            elif value and key == "srcset":
                self.urls.extend(item.strip().split()[0] for item in value.split(",") if item.strip())


def markdown_urls(text):
    parser = MarkdownIt("commonmark", {"html": True})
    def visit(tokens):
        for token in tokens:
            if token.type in {"image", "link_open"}:
                yield token.attrGet("src") or token.attrGet("href")
            if token.type in {"html_inline", "html_block"}:
                html = MediaHTML()
                html.feed(token.content)
                yield from html.urls
            if token.children:
                yield from visit(token.children)
    yield from visit(parser.parse(text))


def linked(path):
    return path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction())


def is_markdown(path):
    return path.suffix.lower() in {".md", ".markdown"}


def markdown_files(project, scopes=None):
    """Yield Markdown files under the project, optionally limited to files/directories."""
    seen = set()
    roots = [project] if not scopes else [(project / s).resolve() for s in scopes]
    for root in roots:
        if not root.is_relative_to(project):
            raise ValueError(f"Audit scope escapes project: {root}")
        if root.is_file():
            candidates = [root] if is_markdown(root) else []
        elif root.is_dir():
            candidates = []
            for folder, dirs, files in os.walk(root, followlinks=False):
                dirs[:] = [d for d in dirs if d not in SKIP and not linked(Path(folder) / d)]
                candidates.extend(Path(folder) / f for f in files if is_markdown(Path(folder) / f))
        else:
            raise ValueError(f"Audit scope not found: {root}")
        for md in candidates:
            if md not in seen:
                seen.add(md)
                yield md


def git_tracked(project):
    """Files versioned by the enclosing Git repository are exempt from Drive storage."""
    try:
        top = subprocess.run(["git", "-C", str(project), "rev-parse", "--show-toplevel"],
                             capture_output=True, text=True, check=True).stdout.strip()
        listed = subprocess.run(["git", "-C", top, "ls-files", "-z"], capture_output=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        return set()
    root = Path(top).resolve()
    return {os.path.normcase(str(root / p)) for p in listed.decode("utf-8").split("\0") if p}


def references(project, scopes=None):
    found = []
    tracked = git_tracked(project)
    storage = (project / ".gdrive").resolve()
    assistant = storage / "managed"
    for md in markdown_files(project, scopes):
        for url in markdown_urls(md.read_text(encoding="utf-8-sig")):
            parsed = urlsplit(url)
            if parsed.scheme or parsed.netloc or not parsed.path:
                continue
            path = (md.parent / unquote(parsed.path)).resolve()
            if path.suffix.lower() in NON_ASSETS:
                continue
            managed = any(path.is_relative_to(assistant / b) for b in BUCKETS)
            human = path.is_relative_to(storage) and not path.is_relative_to(assistant)
            git = not path.is_relative_to(storage) and os.path.normcase(str(path)) in tracked
            owner = ("human" if human else "assistant" if path.is_relative_to(assistant)
                     else "git" if git else "outside")
            found.append({"markdown": md.relative_to(project).as_posix(), "url": url,
                          "path": str(path), "exists": path.is_file(), "managed": managed,
                          "stored": managed or human, "compliant": managed or human or git,
                          "owner": owner})
    return found


def link_target(project):
    """Return the Drive directory behind project/.gdrive, or None when .gdrive is not a link."""
    storage = project / ".gdrive"
    if not os.path.lexists(storage):
        return None
    target = storage.resolve()
    return None if target == project.resolve() / ".gdrive" else target


def make_link(link, target):
    """Directory symlink first; on Windows without symlink rights fall back to a junction."""
    try:
        os.symlink(target, link, target_is_directory=True)
        return "symlink"
    except OSError:
        if os.name != "nt":
            raise
    done = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True)
    if done.returncode or link.resolve() != target:
        raise ValueError("Cannot link .gdrive; enable Windows Developer Mode or create the junction manually")
    return "junction"


def load_config(project):
    target = link_target(project)
    if target is None:
        if (project / ".gdrive").is_dir():
            raise ValueError(".gdrive is a local directory; run init --adopt-local to move it into Drive and link it")
        raise ValueError(".gdrive link missing; run init")
    if not target.is_dir():
        raise ValueError(f"Google Drive unavailable behind .gdrive: {target}")
    config = json.loads(inside(target, "managed/config.json").read_text(encoding="utf-8"))
    if config.get("schema") != 3:
        raise ValueError("Unsupported config schema; expected 3 (linked .gdrive)")
    return target, config


def asset_key(value):
    key = relative(value)
    if key.parts[0] not in BUCKETS or len(key.parts) < 2:
        raise ValueError("Asset key must start with approved/, pending/ or references/")
    return key


def validate_revision(path):
    match = REVISION.search(path.stem)
    if not match or digest(path) != match.group(1):
        raise ValueError(f"Invalid or modified content-addressed revision; re-import with put: {path}")


def adopt_local(project, storage, remote_managed):
    """Copy a legacy local .gdrive/managed into Drive, keep the old tree under .tmp, never overwrite."""
    extra = [p.name for p in storage.iterdir() if p.name != "managed"]
    if extra:
        raise ValueError(f"Human directories in local .gdrive need a manual move first: {extra}")
    local = storage / "managed"
    copied, retired = [], []
    for path in sorted(p for p in local.rglob("*") if p.is_file()):
        rel = path.relative_to(local)
        if rel.parts[0] in BUCKETS:
            validate_revision(path)
        elif rel.parts[0] != "receipts":
            retired.append(rel.as_posix())  # history/, the old per-machine config and strays stay out of Drive
            continue
        copied.append({"key": rel.as_posix(), "action": copy_verified(path, inside(remote_managed, rel))})
    backup = project / ".tmp" / f"gdrive-local-backup-{time.strftime('%Y%m%d-%H%M%S')}"
    backup.parent.mkdir(parents=True, exist_ok=True)
    storage.rename(backup)
    return copied, retired, backup


def run(args):
    project = Path(args.project).resolve()
    if not project.is_dir():
        raise ValueError("Project directory does not exist")
    storage = project / ".gdrive"
    if args.command == "audit":
        rows = references(project, args.doc)
        emit({"scope": args.doc or ["."], "references": rows,
              "unmanaged": sum(not x["compliant"] for x in rows),
              "human_owned": sum(x["owner"] == "human" for x in rows),
              "git_exempt": sum(x["owner"] == "git" for x in rows),
              "missing": sum(not x["exists"] for x in rows),
              "coverage": "Parsed Markdown links/images/reference links and HTML src/href/poster/srcset; Git-tracked files are exempt; dynamic templates require manual review"})
        return int(any(not x["compliant"] or not x["exists"] for x in rows))
    if args.command == "init":
        namespace = relative(args.namespace)
        if len(namespace.parts) < 3:
            raise ValueError("Namespace must contain host/group/project")
        mount = Path(args.drive_root).expanduser().resolve()
        if not mount.is_dir():
            raise ValueError("Drive root unavailable; refusing to create a fake mount")
        if mount == project or mount.is_relative_to(project) or project.is_relative_to(mount):
            raise ValueError("Local project and Drive root must be separate directory trees")
        target = inside(mount, namespace)
        current = link_target(project)
        if current is not None and current != target:
            raise ValueError(f".gdrive already links elsewhere: {current}")
        if current is None and os.path.lexists(storage) and not args.adopt_local:
            raise ValueError(".gdrive is a local directory; rerun with --adopt-local to move it into Drive")
        remote_managed = target / "managed"
        config_path = remote_managed / "config.json"
        if config_path.is_file():
            shared = json.loads(config_path.read_text(encoding="utf-8"))
            if shared.get("schema") != 3 or shared.get("namespace") != namespace.as_posix():
                raise ValueError(f"Drive config mismatch; inspect {config_path}")
        for folder in (*BUCKETS, "receipts"):
            (remote_managed / folder).mkdir(parents=True, exist_ok=True)
        if not config_path.is_file():
            save_json(config_path, {"schema": 3, "namespace": namespace.as_posix()})
        report = {"namespace": namespace.as_posix(), "drive_target": str(target)}
        if current is None:
            if os.path.lexists(storage):
                copied, retired, backup = adopt_local(project, storage, remote_managed)
                report.update(adopted=copied, kept_out_of_drive=retired, local_backup=str(backup))
            report["link"] = make_link(storage, target)
        else:
            report["link"] = "existing"
        ignore = project / ".gitignore"
        existing = ignore.read_bytes().decode("utf-8") if ignore.exists() else ""
        additions = [s for s in ("/.gdrive", "/.tmp/") if s not in existing.splitlines()]
        if additions:
            eol = "\r\n" if "\r\n" in existing else "\n"  # keep the file's own line endings
            lead = eol if existing and not existing.endswith("\n") else ""
            with ignore.open("a", encoding="utf-8", newline="") as f:
                f.write(lead + eol.join(additions) + eol)
        emit(report)
        return 0
    target, config = load_config(project)
    managed = inside(target, "managed")
    if args.command == "status":
        emit({"namespace": config["namespace"], "drive_target": str(target), "mount_available": True,
              "cloud_status": "unknown: filesystem access does not prove upload or second-machine sync"})
        return 0
    if args.command == "put":
        source = Path(args.source).resolve()
        if not source.is_file() or source.is_symlink():
            raise ValueError("Source must be a regular file")
        if args.bucket == "approved" and not (args.reviewer and args.evidence):
            raise ValueError("approved requires --reviewer and --evidence; never invent approval")
        logical = relative(args.name or source.name)
        sha = digest(source)
        key = Path(args.bucket) / logical.parent / f"{logical.stem}--{sha}{logical.suffix.lower()}"
        dest = inside(managed, key)
        result = copy_verified(source, dest, sha)
        entry = {"reviewer": args.reviewer, "evidence": args.evidence, "reason": args.reason}
        receipt_path = inside(managed, Path("receipts") / (key.as_posix() + ".json"))
        old = json.loads(receipt_path.read_text(encoding="utf-8")) if receipt_path.is_file() else {}
        imports = old.get("imports") or ([{k: old.get(k) for k in entry}] if old else [])
        receipt = {"key": key.as_posix(), "sha256": sha, "imports": [*imports, entry]}
        save_json(receipt_path, receipt)
        warning = ({"path_length_warning": f"{len(str(dest))} chars; enable Windows long paths or shorten --name"}
                   if os.name == "nt" and len(str(dest)) > LONG_PATH else {})
        emit({**receipt, **entry, "result": result, "source_preserved": True,
              "cloud_status": "written to Drive mount; upload unverified",
              "markdown_path_from_project": ".gdrive/managed/" + key.as_posix(), **warning})
        return 0
    if args.command == "verify":
        needs_review = []
        if args.key:
            keys = [asset_key(k) for k in args.key]
        else:
            keys = []
            for p in (p for b in BUCKETS for p in (managed / b).rglob("*") if p.is_file()):
                # Drive conflict copies, interrupted transfers and hand-placed files need a human decision.
                (keys if REVISION.search(p.stem) else needs_review).append(p.relative_to(managed))
        verified, invalid = [], []
        for key in keys:
            path = inside(managed, key)
            try:
                if not path.is_file():
                    raise ValueError("Missing asset")
                validate_revision(path)
                verified.append(key.as_posix())
            except ValueError as error:
                invalid.append({"key": key.as_posix(), "error": str(error)})
        emit({"verified": verified, "invalid": invalid, "needs_review": [k.as_posix() for k in needs_review],
              "cloud_status": "unverified"})
        return int(bool(invalid or needs_review))
    if args.command == "remove":
        key = asset_key(args.key)
        source = inside(managed, key)
        if any(Path(r["path"]) == source.resolve() for r in references(project)):
            raise ValueError("Still referenced by Markdown; cannot remove")
        if not source.is_file():
            raise ValueError(f"Missing asset: {key}")
        validate_revision(source)
        kept = inside(project / ".tmp", Path("gdrive-removed") / key) if args.keep_tmp else None
        receipt = inside(managed, Path("receipts") / (key.as_posix() + ".json"))
        if args.apply:
            if kept:
                copy_verified(source, kept)
            # Delete only this explicit object; the Drive client propagates the deletion to other machines.
            source.unlink()
            receipt.unlink(missing_ok=True)
        emit({"key": key.as_posix(), "applied": args.apply, "kept_in_tmp": str(kept) if kept else None,
              "cloud_deletion_status": "unverified"})
        return 0
    raise ValueError("Unknown command")


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--project", default=".")
    sub = p.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init")
    init.add_argument("--drive-root", required=True)
    init.add_argument("--namespace", required=True)
    init.add_argument("--adopt-local", action="store_true",
                      help="Move an existing local .gdrive/managed into Drive, back it up under .tmp, then link")
    audit = sub.add_parser("audit")
    audit.add_argument("--doc", action="append", help="Markdown file or directory relative to --project; repeatable")
    sub.add_parser("status")
    put = sub.add_parser("put")
    put.add_argument("source")
    put.add_argument("--bucket", choices=BUCKETS, default="pending")
    put.add_argument("--name", help="Logical project-relative asset name; SHA-256 revision is appended")
    put.add_argument("--reason", default="explicit")
    put.add_argument("--reviewer")
    put.add_argument("--evidence")
    verify = sub.add_parser("verify")
    verify.add_argument("--key", action="append")
    remove = sub.add_parser("remove")
    remove.add_argument("key")
    remove.add_argument("--apply", action="store_true")
    remove.add_argument("--keep-tmp", action="store_true", help="Copy the removed revision to .tmp/gdrive-removed/")
    try:
        return run(p.parse_args())
    except (ValueError, OSError, KeyError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
