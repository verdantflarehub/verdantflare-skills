"""Private, pinned official CLI for account administrators. Team generation uses Studio."""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request

VERSION = "1.1.26"
DIGESTS = {"windows": "70116002288bf9f4acfe9bfc711c80e8de5d4f8f099634fdf5e903d8434465c1",
           "linux": "5d666fae70c99b7388690191a649d1487962250ed50820e079edfd1ffac7bf5b"}
ROOT = Path(__file__).resolve().parents[1] / ".runtime" / "higgsfield"
STATE = ROOT / "auth"
PROXIES = {"http://127.0.0.1:19081", "http://openclash.openclash.svc.cluster.local:1081"}


class AdminError(RuntimeError):
    pass


def target():
    system = platform.system().lower()
    if system not in DIGESTS or platform.machine().lower() not in {"amd64", "x86_64"}:
        raise AdminError("unsupported_platform")
    return system, ROOT / VERSION / (system + "-amd64") / ("hf.exe" if system == "windows" else "hf")


def install(archive=None):
    system, executable = target()
    if archive:
        data = Path(archive).read_bytes()
    else:
        url = f"https://github.com/higgsfield-ai/cli/releases/download/v{VERSION}/hf_{VERSION}_{system}_amd64.tar.gz"
        with urllib.request.urlopen(url, timeout=120) as response:
            data = response.read(128 * 1024 * 1024 + 1)
    if hashlib.sha256(data).hexdigest() != DIGESTS[system]:
        raise AdminError("official_archive_integrity_failed")
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as package:
        matches = [m for m in package.getmembers() if m.isfile() and Path(m.name).name == executable.name]
        if len(matches) != 1:
            raise AdminError("official_archive_layout_invalid")
        with package.extractfile(matches[0]) as stream:
            binary = stream.read(128 * 1024 * 1024 + 1)
    if len(binary) > 128 * 1024 * 1024:
        raise AdminError("official_binary_size_exceeded")
    executable.parent.mkdir(parents=True, exist_ok=True)
    if executable.exists():
        if hashlib.sha256(executable.read_bytes()).digest() != hashlib.sha256(binary).digest():
            raise AdminError("private_binary_conflict")
    else:
        with executable.open("xb") as stream:
            stream.write(binary)
        executable.chmod(0o700)
    return {"installed": True, "version": VERSION, "platform": system + "-amd64"}


def protect_state():
    STATE.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name != "nt":
        STATE.chmod(0o700)
        return
    system32 = Path(os.environ["SystemRoot"]) / "System32"
    result = subprocess.run([str(system32 / "whoami.exe"), "/user", "/fo", "csv", "/nh"],
                            capture_output=True, text=True, check=True)
    row = next(csv.reader(io.StringIO(result.stdout.strip())))
    sid = row[-1]
    if not re.fullmatch(r"S-1-[0-9-]+", sid):
        raise AdminError("state_acl_identity_unavailable")
    result = subprocess.run([str(system32 / "icacls.exe"), str(STATE), "/inheritance:r", "/grant:r",
                            f"*{sid}:(OI)(CI)F", "*S-1-5-18:(OI)(CI)F"], capture_output=True)
    if result.returncode:
        raise AdminError("state_acl_failed")


def environment(proxy):
    if proxy not in PROXIES:
        raise AdminError("explicit_dmit_proxy_required")
    env = {k: v for k, v in os.environ.items() if not k.upper().endswith("_PROXY") and not k.startswith("HIGGSFIELD_")}
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        env[key] = proxy
    env.update(NO_PROXY="localhost,127.0.0.1,::1", no_proxy="localhost,127.0.0.1,::1",
               HIGGSFIELD_CREDENTIALS_PATH=str(STATE / "credentials.json"), HIGGSFIELD_CONFIG_PATH=str(STATE / "config.json"),
               HIGGSFIELD_DISABLE_TELEMETRY="1", HIGGSFIELD_NO_UPDATE_CHECK="1")
    if os.name == "nt":
        env["PATH"] = str(Path(os.environ["SystemRoot"]) / "System32") + os.pathsep + env.get("PATH", "")
    return env


def run_cli(args, proxy, *, login=False):
    if not login and any((STATE / name).exists() for name in ("server-owned.json", "handover-pending.json")):
        raise AdminError("credentials_owned_by_server")
    protect_state()
    _, executable = target()
    if not executable.is_file():
        raise AdminError("private_cli_not_installed")
    env = environment(proxy)
    if login:
        # A new browser grant must not reuse or refresh a server-owned local copy.
        with tempfile.TemporaryDirectory(prefix=".login-", dir=STATE) as directory:
            pending = Path(directory)
            env.update(HIGGSFIELD_CREDENTIALS_PATH=str(pending / "credentials.json"),
                       HIGGSFIELD_CONFIG_PATH=str(pending / "config.json"))
            try:
                result = subprocess.run([str(executable), *args, "--json", "--no-color"], env=env,
                                        capture_output=True, timeout=900)
            except subprocess.TimeoutExpired:
                raise AdminError("official_cli_login_timed_out") from None
            if result.returncode or not (pending / "credentials.json").is_file():
                raise AdminError("official_cli_login_failed")
            if not (pending / "config.json").exists():
                (pending / "config.json").write_text("{}", encoding="utf-8")
            os.replace(pending / "config.json", STATE / "config.json")
            os.replace(pending / "credentials.json", STATE / "credentials.json")
        (STATE / "server-owned.json").unlink(missing_ok=True)
        (STATE / "handover-pending.json").unlink(missing_ok=True)
        return {"authenticated": True, "workspace_selection_required": True}
    result = subprocess.run([str(executable), *args, "--json", "--no-color"], env=env,
                            capture_output=True, timeout=60)
    if result.returncode:
        raise AdminError("official_cli_operation_failed")
    if args[:2] in (["workspace", "set"], ["workspace", "unset"]):
        return {"workspace_selected": True}
    return json.loads(result.stdout)


def importer_command(pod, container):
    if not all(re.fullmatch(r"[a-z0-9][a-z0-9.-]{0,252}", value) for value in (pod, container)):
        raise AdminError("invalid_kubernetes_target")
    kubectl = shutil.which("kubectl")
    if not kubectl and os.name == "nt":
        candidate = Path(os.environ.get("ProgramFiles", "")) / "Docker/Docker/resources/bin/kubectl.exe"
        if candidate.is_file():
            kubectl = str(candidate)
    if not kubectl:
        raise AdminError("kubectl_client_unavailable")
    return [kubectl, "--context", "dev.verdantflarehub.com", "-n", "verdantflare-video", "exec", "-i", pod,
               "-c", container, "--", "python", "-m", "higgsfield_gateway.credentials", "--state-dir", "/data/higgsfield"]


def confirm_handover(receipt, pending):
    if (receipt.get("imported") is not True or receipt.get("revision") != pending["revision"] or
            receipt.get("workspace_id", pending["workspace_id"]) != pending["workspace_id"]):
        raise AdminError("credential_handover_unconfirmed")
    path = STATE / "server-owned.json"
    with path.open("w", encoding="utf-8") as stream:
        json.dump({"revision": receipt["revision"], "context": "dev.verdantflarehub.com"}, stream)
        stream.flush()
        os.fsync(stream.fileno())
    (STATE / "handover-pending.json").unlink()
    return {"handed_over": True, "local_refresh_disabled": True}


def reconcile(pod, container):
    pending = json.loads((STATE / "handover-pending.json").read_text(encoding="utf-8"))
    if not re.fullmatch(r"[0-9a-f]{64}", pending.get("revision", "")):
        raise AdminError("invalid_credential_revision")
    command = importer_command(pod, container) + ["--receipt", "--expected-revision", pending["revision"]]
    result = subprocess.run(command, input=b"", capture_output=True, timeout=60)
    if result.returncode:
        raise AdminError("credential_handover_unconfirmed")
    return confirm_handover(json.loads(result.stdout), pending)


def handover(pod, container, workspace, expected_revision=None):
    if any((STATE / name).exists() for name in ("server-owned.json", "handover-pending.json")):
        raise AdminError("credentials_already_handed_over")
    payload = {"credentials": json.loads((STATE / "credentials.json").read_text(encoding="utf-8")),
               "config": json.loads((STATE / "config.json").read_text(encoding="utf-8")), "workspace_id": workspace}
    command = importer_command(pod, container)
    if expected_revision:
        if not re.fullmatch(r"[0-9a-f]{64}", expected_revision):
            raise AdminError("invalid_credential_revision")
        command.extend(["--expected-revision", expected_revision])
    pending = STATE / "handover-pending.json"
    expected = hashlib.sha256(json.dumps(payload["credentials"], separators=(",", ":")).encode()).hexdigest()
    with pending.open("x", encoding="utf-8") as stream:
        json.dump({"revision": expected, "context": "dev.verdantflarehub.com", "workspace_id": workspace}, stream)
        stream.flush()
        os.fsync(stream.fileno())
    # Uncertain transport outcomes retain this marker and prohibit local refresh.
    result = subprocess.run(command, input=json.dumps(payload).encode(), capture_output=True, timeout=60)
    if result.returncode:
        raise AdminError("credential_handover_failed")
    receipt = json.loads(result.stdout)
    return confirm_handover(receipt, {"revision": expected, "workspace_id": workspace})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("install").add_argument("--archive")
    commands.add_parser("version")
    for operation in ("login", "workspace", "account"):
        child = commands.add_parser(operation)
        child.add_argument("--proxy", choices=sorted(PROXIES), required=True)
        if operation == "workspace":
            child.add_argument("workspace_id")
    child = commands.add_parser("handover")
    for option in ("pod", "container", "workspace"):
        child.add_argument("--" + option, required=True)
    child.add_argument("--expected-revision")
    child = commands.add_parser("reconcile")
    for option in ("pod", "container"):
        child.add_argument("--" + option, required=True)
    args = parser.parse_args()
    try:
        if args.command == "install":
            value = install(args.archive)
        elif args.command == "version":
            _, executable = target()
            result = subprocess.run([str(executable), "version", "--no-color"], capture_output=True, text=True, timeout=10)
            if result.returncode or f"higgsfield {VERSION} " not in result.stdout:
                raise AdminError("private_cli_version_mismatch")
            value = {"version": VERSION, "verified": True}
        elif args.command == "login":
            value = run_cli(["auth", "login"], args.proxy, login=True)
        elif args.command == "workspace":
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", args.workspace_id):
                raise AdminError("invalid_workspace")
            run_cli(["workspace", "unset"] if args.workspace_id == "personal" else
                    ["workspace", "set", args.workspace_id], args.proxy)
            value = {"workspace_selected": True}
        elif args.command == "account":
            data = run_cli(["account", "status"], args.proxy)
            value = {"credits": data["credits"], "plan": data.get("subscription_plan_type")}
        elif args.command == "reconcile":
            value = reconcile(args.pod, args.container)
        else:
            value = handover(args.pod, args.container, args.workspace, args.expected_revision)
        print(json.dumps(value))
    except (AdminError, OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        print(json.dumps({"error": str(error) if isinstance(error, AdminError) else "admin_operation_failed"}), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
