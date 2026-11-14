"""Manage the optional, reversible Windows VS Code workspace-session UI patch."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import tempfile

RESOURCES = Path(__file__).resolve().parent.parent / "resources" / "workspace-sessions"
SIDECAR = "webview/assets/verdantflare-workspace-sessions.mjs"


class PatchError(RuntimeError):
    pass


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def atomic_write(path: Path, data: bytes) -> None:
    fd, name = tempfile.mkstemp(prefix=".vf-sessions-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def render_header(original: bytes, adapter: dict | None = None) -> bytes:
    text = original.decode("utf-8")
    replacements = [
        ("wi as z,", "wi as vfRawQuery,"),
        ("IN as lt,", "IN as vfRawCloud,"),
        ("function kn(e,t,n){let r=Xe()", "function kn(e,t,n){let vfContext=vfUseContext();let r=Xe()"),
        ("clientThreadIdsByConversationId:c}),u.current)",
         "clientThreadIdsByConversationId:c}).filter(task=>vfAllowsTask(task,vfContext.roots,vfContext.canonical)),u.current)"),
        ("[e,n,c,l,t,r,i])", "[e,n,c,l,t,r,i,vfContext])"),
        ("o=i===void 0||i,c=te()", "o=false,c=te()"),
        ("children:[M,N,F]", "children:[M,vfNoticeElement(),N,F]"),
    ]
    if adapter is not None:
        replacements = adapter.get("replacements", replacements)
    for old, new in replacements:
        if text.count(old) != 1:
            raise PatchError("Unsupported header structure; no files changed")
        text = text.replace(old, new, 1)
    bindings = (adapter or {}).get("bindings", "bindings.js.txt")
    return (RESOURCES.joinpath(bindings).read_text("utf-8") + "\n" + text).encode("utf-8")


def discover_extension() -> Path:
    root = Path.home() / ".vscode/extensions"
    obsolete = json.loads((root / ".obsolete").read_text("utf-8")) if (root / ".obsolete").exists() else {}
    candidates = [p for p in root.glob("openai.chatgpt-*") if p.is_dir() and not obsolete.get(p.name)]
    if not candidates:
        raise PatchError("No active Codex VS Code extension found")
    # VS Code's registry identifies the selected installation, unlike directory mtime.
    registry = root / "extensions.json"
    if registry.exists():
        selected = [item.get("relativeLocation") for item in json.loads(registry.read_text("utf-8"))
                    if item.get("identifier", {}).get("id") == "openai.chatgpt"]
        active = [p for p in candidates if p.name in selected]
        if len(active) == 1:
            return active[0].resolve()
    if len(candidates) != 1:
        raise PatchError("Multiple Codex installations; specify --extension")
    return candidates[0].resolve()


def installation_status(extension: Path) -> dict:
    """Read-only status for installers, including when patching was not requested."""
    try:
        patch = Patch(extension)
        return {"status": patch.probe(), "version": patch.adapter["version"]}
    except PatchError as error:
        return {"status": "blocked", "reason": str(error)}
    except (OSError, ValueError, KeyError):
        return {"status": "blocked", "reason": "Invalid or unreadable installation/state files"}


class Patch:
    def __init__(self, extension: Path, state_root: Path | None = None):
        self.extension = extension.resolve(strict=True)
        self.adapter = json.loads((RESOURCES / "adapter.json").read_text("utf-8"))
        version = json.loads((self.extension / "package.json").read_text("utf-8")).get("version")
        for candidate in RESOURCES.glob("adapter-*.json"):
            adapter = json.loads(candidate.read_text("utf-8"))
            if adapter["version"] == version:
                self.adapter = adapter
                break
        base = state_root or Path(os.environ.get("LOCALAPPDATA", Path.home() / ".local/share")) / "verdantflare-agent-codex/workspace-sessions"
        self.state = base.resolve() / sha(str(self.extension).casefold().encode())[:20]
        self.target = self.extension / self.adapter["target"]
        self.sidecar = self.extension / SIDECAR
        self.manifest_path = self.state / "manifest.json"
        self.backup = self.state / "original-header.js"

    def check_environment(self):
        if os.name != "nt":
            raise PatchError("This adapter supports Windows local VS Code only")
        package = json.loads((self.extension / "package.json").read_text("utf-8"))
        if package.get("publisher") != "openai" or package.get("name") != "chatgpt":
            raise PatchError("Not the OpenAI Codex extension")
        if package.get("version") != self.adapter["version"]:
            raise PatchError(f"Workspace-session patch is not adapted to extension {package.get('version')}; no files changed")
        for relative, expected in self.adapter["dependencies"].items():
            path = self.extension / relative
            if not path.is_file() or sha(path.read_bytes()) != expected:
                raise PatchError("Extension dependency changed; no files changed")

    def manifest(self):
        if not self.manifest_path.exists():
            return None
        result = json.loads(self.manifest_path.read_text("utf-8"))
        if result.get("version") != self.adapter["version"] or result.get("original") != self.adapter["target_sha256"]:
            raise PatchError("Invalid patch manifest")
        if not self.backup.is_file() or sha(self.backup.read_bytes()) != result["original"]:
            raise PatchError("Backup integrity check failed")
        return result

    def probe(self):
        self.check_environment()
        current = sha(self.target.read_bytes())
        manifest = self.manifest()
        sidecar = sha(self.sidecar.read_bytes()) if self.sidecar.exists() else None
        if current == self.adapter["target_sha256"] and sidecar is None:
            self.render(self.target.read_bytes())
            return "compatible"
        if manifest and current == manifest["patched"] and sidecar == manifest["sidecar"]:
            expected = sha(self.render(self.backup.read_bytes()))
            resource = sha((RESOURCES / "filter.mjs").read_bytes())
            return "installed" if expected == current and resource == sidecar else "installed-older-patch"
        if manifest and current in (manifest["original"], manifest["patched"]) and sidecar in (None, manifest["sidecar"]):
            return "interrupted"
        raise PatchError("Extension files differ from known original/patch; refusing overwrite")

    @contextmanager
    def lock(self):
        self.state.mkdir(parents=True, exist_ok=True)
        path = self.state / "operation.lock"
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError as exc:
            raise PatchError("Another patch operation or interrupted lock exists; inspect before retrying") from exc
        try:
            with os.fdopen(fd, "w") as stream:
                stream.write(str(os.getpid()))
            yield
        finally:
            path.unlink(missing_ok=True)

    def render(self, original: bytes) -> bytes:
        # Preserve the legacy adapter and its existing backup hashes exactly.
        if "replacements" not in self.adapter:
            return render_header(original)
        return render_header(original, self.adapter)

    def apply(self):
        with self.lock():
            status = self.probe()
            if status == "installed":
                return "already-installed"
            if status != "compatible":
                raise PatchError("Rollback the known patch before applying this revision")
            original = self.target.read_bytes()
            patched = self.render(original)
            sidecar = (RESOURCES / "filter.mjs").read_bytes()
            atomic_write(self.backup, original)
            manifest = {"version": self.adapter["version"], "original": sha(original),
                        "patched": sha(patched), "sidecar": sha(sidecar)}
            atomic_write(self.manifest_path, (json.dumps(manifest, indent=2) + "\n").encode())
            # Recheck immediately before writing; preserve unrelated concurrent edits.
            if self.target.read_bytes() != original or self.sidecar.exists():
                raise PatchError("Extension changed during preparation; no extension files changed")
            try:
                atomic_write(self.sidecar, sidecar)
                atomic_write(self.target, patched)
                if self.probe() != "installed":
                    raise PatchError("Patch verification failed")
            except BaseException:
                self._restore_known(manifest)
                raise
            return "installed-reload-required"

    def _restore_known(self, manifest):
        current = sha(self.target.read_bytes())
        sidecar = sha(self.sidecar.read_bytes()) if self.sidecar.exists() else None
        if current not in (manifest["original"], manifest["patched"]) or sidecar not in (None, manifest["sidecar"]):
            raise PatchError("Concurrent extension change; recovery stopped to preserve it")
        original = self.backup.read_bytes()
        if sha(original) != manifest["original"]:
            raise PatchError("Backup integrity check failed")
        if current != manifest["original"]:
            atomic_write(self.target, original)
        if self.sidecar.exists():
            self.sidecar.unlink()

    def rollback(self):
        with self.lock():
            self.check_environment()
            manifest = self.manifest()
            if manifest is None:
                if self.probe() == "compatible":
                    return "not-installed"
                raise PatchError("No verified original backup")
            self._restore_known(manifest)
            if self.probe() != "compatible":
                raise PatchError("Rollback verification failed")
            return "restored-reload-required"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["probe", "status", "apply", "rollback"])
    parser.add_argument("--extension", type=Path)
    parser.add_argument("--state-root", type=Path)
    args = parser.parse_args(argv)
    try:
        patch = Patch(args.extension or discover_extension(), args.state_root)
        status = patch.probe() if args.action in ("probe", "status") else getattr(patch, args.action)()
        print(json.dumps({"status": status, "version": patch.adapter["version"]}))
        return 0
    except (PatchError, OSError, ValueError, KeyError) as error:
        # Do not expose personal paths or third-party exception payloads in logs.
        reason = str(error) if isinstance(error, PatchError) else "Invalid or unreadable installation/state files; no unverified overwrite performed."
        print(json.dumps({"status": "blocked", "reason": reason}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
