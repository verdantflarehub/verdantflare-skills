"""Install/update the skill from its repository source; retain previous install."""
import argparse
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import shutil
import tempfile
import json
import os
import re
import subprocess
import zipfile
from xml.sax.saxutils import escape

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


EXTENSION_ROOT = Path(__file__).resolve().parents[1] / "resources" / "vscode-preview"
EXTENSION_FILES = ("package.json", "extension.js", "bridge.js", "preview.js")


def build_vsix(destination, source=EXTENSION_ROOT):
    """Package only the explicit runtime source allowlist, with reproducible ZIP metadata."""
    source = Path(source)
    package = json.loads((source / "package.json").read_text(encoding="utf-8"))
    identity = {key: escape(package[key], {'"': '&quot;'})
                for key in ("name", "publisher", "version", "displayName", "description")}
    engine = escape(package["engines"]["vscode"], {'"': '&quot;'})
    manifest = f'''<?xml version="1.0" encoding="utf-8"?>
<PackageManifest Version="2.0.0" xmlns="http://schemas.microsoft.com/developer/vsx-schema/2011">
<Metadata><Identity Language="en-US" Id="{identity['name']}" Version="{identity['version']}" Publisher="{identity['publisher']}"/>
<DisplayName>{identity['displayName']}</DisplayName><Description xml:space="preserve">{identity['description']}</Description>
<Categories>Other</Categories><Properties><Property Id="Microsoft.VisualStudio.Code.Engine" Value="{engine}"/>
<Property Id="Microsoft.VisualStudio.Code.ExtensionDependencies" Value=""/>
<Property Id="Microsoft.VisualStudio.Code.ExtensionPack" Value=""/></Properties></Metadata>
<Installation><InstallationTarget Id="Microsoft.VisualStudio.Code"/></Installation><Dependencies/>
<Assets><Asset Type="Microsoft.VisualStudio.Code.Manifest" Path="extension/package.json" Addressable="true"/></Assets>
</PackageManifest>'''
    types = '''<?xml version="1.0" encoding="utf-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="json" ContentType="application/json"/>
<Default Extension="js" ContentType="application/javascript"/>
<Default Extension="vsixmanifest" ContentType="text/xml"/></Types>'''
    entries = {"extension.vsixmanifest": manifest.encode(), "[Content_Types].xml": types.encode()}
    entries.update({"extension/" + name: (source / name).read_bytes() for name in EXTENSION_FILES})
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_STORED) as archive:
        for name, data in sorted(entries.items()):
            info = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            archive.writestr(info, data)
    return Path(destination)


def find_code(explicit=None):
    if explicit:
        candidate = shutil.which(explicit) or explicit
        if Path(candidate).is_file():
            return str(Path(candidate).resolve())
        raise ValueError("Specified VS Code CLI does not exist")
    found = shutil.which("code") or shutil.which("code.cmd")
    if found:
        return found
    candidates = [Path(os.environ.get("LOCALAPPDATA", "")) / "Programs/Microsoft VS Code/bin/code.cmd",
                  Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Microsoft VS Code/bin/code.cmd",
                  Path("/Applications/Visual Studio Code.app/Contents/Resources/app/bin/code")]
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    raise ValueError("VS Code CLI not found; use --code-bin")


def run_code(code, args):
    # Windows CLI .cmd wrapper is replaced with its own Node entrypoint, avoiding cmd quoting.
    executable = Path(code)
    env = os.environ.copy()
    command = [str(executable), *args]
    if os.name == "nt" and executable.suffix.lower() in (".cmd", ".bat"):
        root = executable.parent.parent
        app = root / "Code.exe"
        cli = root / "resources/app/out/cli.js"
        # New Windows VS Code installs place resources under a version/hash directory.
        wrapper = executable.read_text(encoding="utf-8")
        match = re.search(r'"%~dp0\.\.\\([^"\r\n]*resources\\app\\out\\cli\.js)"', wrapper, re.I)
        if match:
            cli = (root / match.group(1).replace("\\", "/")).resolve()
            if not cli.is_relative_to(root.resolve()):
                raise ValueError("CLI entrypoint escapes editor installation")
        if not app.is_file() or not cli.is_file():
            raise ValueError("Unsupported CLI wrapper; specify the VS Code bin/code.cmd")
        env["ELECTRON_RUN_AS_NODE"] = "1"
        command = [str(app), str(cli), *args]
    return subprocess.run(command, check=True, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", env=env, timeout=120).stdout


def check_vscode(code):
    package = json.loads((EXTENSION_ROOT / "package.json").read_text(encoding="utf-8"))
    expected = f"{package['publisher']}.{package['name']}@{package['version']}"
    installed = run_code(code, ["--list-extensions", "--show-versions"])
    if expected.lower() not in {line.strip().lower() for line in installed.splitlines()}:
        raise ValueError("Expected preview extension version is not installed: " + expected)
    print("Verified installed extension: " + expected)


def install_vscode(code):
    with tempfile.TemporaryDirectory(prefix="gdrive-vsix-") as temporary:
        archive = build_vsix(Path(temporary) / "gdrive-preview.vsix")
        print("VSIX SHA-256: " + hashlib.sha256(archive.read_bytes()).hexdigest())
        run_code(code, ["--install-extension", str(archive), "--force"])
    check_vscode(code)
    print("Reload the VS Code window to activate the installed version.")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skills-root", action="append",
                        help=f"Global skills root; repeatable. Default: {', '.join(DEFAULT_ROOTS)}")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--vscode", action="store_true", help="Build and install the local preview extension")
    mode.add_argument("--check-vscode", action="store_true", help="Read-only check of installed extension version")
    parser.add_argument("--code-bin", help="Explicit VS Code CLI path")
    args = parser.parse_args(argv)
    code = find_code(args.code_bin) if args.vscode or args.check_vscode else None
    if args.check_vscode:
        check_vscode(code)
        return
    for root in args.skills_root or DEFAULT_ROOTS:
        install(root)
    if args.vscode:
        install_vscode(code)


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        # Do not echo subprocess arguments, local paths or editor diagnostics.
        print(f"Installation failed ({type(exc).__name__}); check CLI availability and extension version.")
        raise SystemExit(1)
