#!/usr/bin/env python3
"""Configuration, dependency bootstrap, and validation for VerdantFlare Video."""

from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


CONFIG_VERSION = "1"
DEFAULT_CONFIG_FILE = "~/.config/verdantflare/video/.env"
DEFAULT_STATE_DIR = "~/.local/state/verdantflare/video"
DEFAULT_API_BASE_URL = "https://api.verdantflarehub.com/v1"
DEFAULT_S3_ENDPOINT = "https://cache.ali.wodcloud.com"
DEFAULT_S3_BUCKET = "verdantflare-video-input"
DEFAULT_S3_PUBLIC_BASE_URL = "https://cache.ali.wodcloud.com"
DEFAULT_S3_PREFIX = "vscode/verdantflare/input"
DEFAULT_POLL_INTERVAL = 8
DEFAULT_TASK_TIMEOUT = 900
DEFAULT_RETRY_LIMIT = 5

MC_RELEASE = "RELEASE.2025-08-13T08-35-41Z"
MC_ASSETS = {
    "arm64": (
        f"https://github.com/minio/mc/releases/download/{MC_RELEASE}/"
        f"mc.darwin-arm64.{MC_RELEASE}",
        "a877fd0c183409da9f20f9d6e1811987298bbbca1aa03428eebdffba79fb9445",
    ),
    "x86_64": (
        f"https://github.com/minio/mc/releases/download/{MC_RELEASE}/"
        f"mc.darwin-amd64.{MC_RELEASE}",
        "2862c79cce11b09be9a8911a279b2e9465bebf74b9f01abca9c348a0d795f0cb",
    ),
}
MC_ALLOWED_REDIRECT_HOSTS = {"github.com", "release-assets.githubusercontent.com"}
CONFIG_ALLOWED_HOSTS = {"api.verdantflarehub.com", "cache.ali.wodcloud.com"}
API_ALLOWED_HOSTS = {"api.verdantflarehub.com"}
S3_ALLOWED_HOSTS = {"cache.ali.wodcloud.com"}
PUBLIC_ALLOWED_HOSTS = {"cache.ali.wodcloud.com"}

CONFIG_KEYS = {
    "VERDANTFLARE_VIDEO_CONFIG_VERSION",
    "VERDANTFLARE_VIDEO_API_BASE_URL",
    "VERDANTFLARE_VIDEO_API_KEY",
    "VERDANTFLARE_VIDEO_S3_ENDPOINT",
    "VERDANTFLARE_VIDEO_S3_ACCESS_KEY",
    "VERDANTFLARE_VIDEO_S3_SECRET_KEY",
    "VERDANTFLARE_VIDEO_S3_BUCKET",
    "VERDANTFLARE_VIDEO_S3_PUBLIC_BASE_URL",
    "VERDANTFLARE_VIDEO_S3_PREFIX",
    "VERDANTFLARE_VIDEO_POLL_INTERVAL_SECONDS",
    "VERDANTFLARE_VIDEO_TASK_TIMEOUT_SECONDS",
    "VERDANTFLARE_VIDEO_TRANSIENT_RETRY_LIMIT",
    "VERDANTFLARE_VIDEO_DEFAULT_OUTPUT_DIR",
    "VERDANTFLARE_VIDEO_STATE_DIR",
}
REQUIRED_KEYS = {
    "VERDANTFLARE_VIDEO_API_KEY",
    "VERDANTFLARE_VIDEO_S3_ACCESS_KEY",
    "VERDANTFLARE_VIDEO_S3_SECRET_KEY",
}
TEST_MODE = os.environ.get("VERDANTFLARE_VIDEO_TEST_MODE") == "1"


class ConfigError(ValueError):
    """Raised for invalid or incomplete configuration."""


class _ValidatedRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Follow only explicitly trusted HTTPS redirects."""

    def __init__(self, allowed_hosts: set[str], *, same_host: str | None = None, max_redirections: int = 1):
        super().__init__()
        self.allowed_hosts = {host.lower().rstrip(".") for host in allowed_hosts}
        self.same_host = same_host.lower().rstrip(".") if same_host else None
        self.max_redirections = max_redirections

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parsed = urllib.parse.urlparse(newurl)
        host = (parsed.hostname or "").lower().rstrip(".")
        try:
            port = parsed.port
        except ValueError as exc:
            raise ConfigError("download redirected to an invalid URL") from exc
        is_test = _is_test_url(parsed)
        if parsed.scheme != "https" or parsed.username or parsed.password or parsed.fragment or (port not in (None, 443) and not is_test):
            if not (is_test and parsed.scheme in {"http", "https"}):
                raise ConfigError("download redirected to an invalid URL")
        if host not in self.allowed_hosts or (self.same_host and host != self.same_host):
            raise ConfigError("download redirected to an untrusted host")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _expand_path(value: str) -> Path:
    path = Path(os.path.expanduser(value))
    if not path.is_absolute():
        raise ConfigError("path must be absolute")
    return path


def parse_env_text(text: str) -> dict[str, str]:
    """Parse the intentionally small, non-shell .env grammar."""
    if len(text.encode("utf-8")) > 32 * 1024:
        raise ConfigError("configuration exceeds 32 KiB")
    values: dict[str, str] = {}
    for line_number, raw_line in enumerate(text.splitlines(), 1):
        line = raw_line.strip("\r")
        if not line or line.lstrip().startswith("#"):
            continue
        if "=" not in line:
            raise ConfigError(f"line {line_number}: expected KEY=VALUE")
        key, value = line.split("=", 1)
        if key != key.strip() or not re.fullmatch(r"[A-Z][A-Z0-9_]*", key):
            raise ConfigError(f"line {line_number}: invalid field name")
        if key == "VERDANTFLARE_VIDEO_ENV_FILE" or key not in CONFIG_KEYS:
            raise ConfigError(f"line {line_number}: unknown field {key}")
        if key in values:
            raise ConfigError(f"line {line_number}: duplicate field {key}")
        if "\x00" in value or "\n" in value or "\r" in value:
            raise ConfigError(f"line {line_number}: invalid value")
        if "$" in value and ("$(" in value or "${" in value):
            raise ConfigError(f"line {line_number}: shell expansion syntax is not allowed")
        values[key] = value
    return values


def read_env_file(path: Path) -> dict[str, str]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"cannot read configuration: {path}") from exc
    return parse_env_text(text)


def _is_test_url(url: urllib.parse.ParseResult) -> bool:
    return TEST_MODE and url.hostname in {"127.0.0.1", "localhost", "::1"}


def validate_url(value: str, allowed_hosts: set[str], field: str, *, allow_path=True) -> str:
    if any(ord(char) < 0x20 or ord(char) == 0x7F for char in value):
        raise ConfigError(f"{field} contains control characters")
    parsed = urllib.parse.urlparse(value)
    if parsed.scheme != "https" or parsed.username or parsed.password or parsed.fragment:
        if not _is_test_url(parsed) or parsed.scheme not in {"http", "https"}:
            raise ConfigError(f"{field} must be an HTTPS URL without credentials or fragment")
    if parsed.query:
        raise ConfigError(f"{field} must not contain a query")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ConfigError(f"{field} has an invalid port") from exc
    if port not in (None, 443) and not _is_test_url(parsed):
        raise ConfigError(f"{field} must use HTTPS port 443")
    host = (parsed.hostname or "").lower().rstrip(".")
    if host not in allowed_hosts and not _is_test_url(parsed):
        raise ConfigError(f"{field} host is not allowed")
    if not allow_path and parsed.path not in ("", "/"):
        raise ConfigError(f"{field} must not contain a path")
    return urllib.parse.urlunparse(parsed._replace(fragment=""))


def _int_value(values: Mapping[str, str], key: str, default: int, low: int, high: int) -> int:
    raw = values.get(key, str(default))
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(f"{key} must be an integer") from exc
    if not low <= value <= high:
        raise ConfigError(f"{key} must be between {low} and {high}")
    return value


def installation_id(access_key: str) -> str:
    digest = hashlib.sha256(access_key.encode("utf-8")).hexdigest()[:24]
    return f"ak-{digest}"


@dataclass(frozen=True)
class Config:
    config_file: Path
    state_dir: Path
    api_base_url: str
    api_key: str
    s3_endpoint: str
    s3_access_key: str
    s3_secret_key: str
    s3_bucket: str
    s3_public_base_url: str
    s3_prefix: str
    poll_interval: int
    task_timeout: int
    retry_limit: int
    default_output_dir: Path | None

    @property
    def tasks_dir(self) -> Path:
        return self.state_dir / "tasks"

    @property
    def quarantine_dir(self) -> Path:
        return self.tasks_dir / "quarantine"

    @property
    def submissions_dir(self) -> Path:
        return self.state_dir / "submissions"

    @property
    def locks_dir(self) -> Path:
        return self.state_dir / "locks"

    @property
    def mc_path(self) -> Path:
        return self.state_dir / "runtime" / "bin" / "mc"

    @property
    def upload_prefix(self) -> str:
        return f"{self.s3_prefix}/{installation_id(self.s3_access_key)}"

    def ensure_dirs(self) -> None:
        for path in (self.state_dir, self.tasks_dir, self.quarantine_dir, self.submissions_dir, self.locks_dir):
            path.mkdir(mode=0o700, parents=True, exist_ok=True)
            os.chmod(path, 0o700)

    def redacted(self) -> dict[str, object]:
        return {
            "config_file": str(self.config_file),
            "state_dir": str(self.state_dir),
            "api_base_url": self.api_base_url,
            "s3_endpoint": self.s3_endpoint,
            "s3_bucket": self.s3_bucket,
            "s3_prefix": self.s3_prefix,
            "installation_id": installation_id(self.s3_access_key),
            "poll_interval": self.poll_interval,
            "task_timeout": self.task_timeout,
            "retry_limit": self.retry_limit,
        }


def build_config(values: Mapping[str, str], *, config_file: Path | None = None) -> Config:
    unknown = set(values) - CONFIG_KEYS
    if unknown:
        raise ConfigError(f"unknown fields: {', '.join(sorted(unknown))}")
    missing = REQUIRED_KEYS - set(values)
    if missing:
        raise ConfigError("missing required fields: " + ", ".join(sorted(missing)))
    for key in REQUIRED_KEYS:
        if not values[key] or values[key].strip() != values[key] or "\n" in values[key]:
            raise ConfigError(f"{key} must be a non-empty single-line value")
    version = values.get("VERDANTFLARE_VIDEO_CONFIG_VERSION", CONFIG_VERSION)
    if version != CONFIG_VERSION:
        raise ConfigError(f"unsupported config version: {version}")
    api_base = validate_url(
        values.get("VERDANTFLARE_VIDEO_API_BASE_URL", DEFAULT_API_BASE_URL),
        API_ALLOWED_HOSTS,
        "VERDANTFLARE_VIDEO_API_BASE_URL",
    ).rstrip("/")
    if not urllib.parse.urlparse(api_base).path.endswith("/v1"):
        raise ConfigError("VERDANTFLARE_VIDEO_API_BASE_URL path must end with /v1")
    s3_endpoint = validate_url(
        values.get("VERDANTFLARE_VIDEO_S3_ENDPOINT", DEFAULT_S3_ENDPOINT),
        S3_ALLOWED_HOSTS,
        "VERDANTFLARE_VIDEO_S3_ENDPOINT",
    ).rstrip("/")
    public_base = validate_url(
        values.get("VERDANTFLARE_VIDEO_S3_PUBLIC_BASE_URL", DEFAULT_S3_PUBLIC_BASE_URL),
        PUBLIC_ALLOWED_HOSTS,
        "VERDANTFLARE_VIDEO_S3_PUBLIC_BASE_URL",
    ).rstrip("/")
    bucket = values.get("VERDANTFLARE_VIDEO_S3_BUCKET", DEFAULT_S3_BUCKET)
    if not re.fullmatch(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]", bucket):
        raise ConfigError("VERDANTFLARE_VIDEO_S3_BUCKET is not DNS-compatible")
    prefix = values.get("VERDANTFLARE_VIDEO_S3_PREFIX", DEFAULT_S3_PREFIX)
    if not prefix or prefix.startswith("/") or prefix.endswith("/") or not re.fullmatch(r"[a-z0-9._/-]+", prefix):
        raise ConfigError("invalid S3 prefix")
    state_dir = _expand_path(values.get("VERDANTFLARE_VIDEO_STATE_DIR", DEFAULT_STATE_DIR))
    output_raw = values.get("VERDANTFLARE_VIDEO_DEFAULT_OUTPUT_DIR", "")
    output_dir = _expand_path(output_raw) if output_raw else None
    return Config(
        config_file=config_file or _expand_path(DEFAULT_CONFIG_FILE),
        state_dir=state_dir,
        api_base_url=api_base,
        api_key=values["VERDANTFLARE_VIDEO_API_KEY"],
        s3_endpoint=s3_endpoint,
        s3_access_key=values["VERDANTFLARE_VIDEO_S3_ACCESS_KEY"],
        s3_secret_key=values["VERDANTFLARE_VIDEO_S3_SECRET_KEY"],
        s3_bucket=bucket,
        s3_public_base_url=public_base,
        s3_prefix=prefix,
        poll_interval=_int_value(values, "VERDANTFLARE_VIDEO_POLL_INTERVAL_SECONDS", DEFAULT_POLL_INTERVAL, 3, 60),
        task_timeout=_int_value(values, "VERDANTFLARE_VIDEO_TASK_TIMEOUT_SECONDS", DEFAULT_TASK_TIMEOUT, 60, 3600),
        retry_limit=_int_value(values, "VERDANTFLARE_VIDEO_TRANSIENT_RETRY_LIMIT", DEFAULT_RETRY_LIMIT, 0, 10),
        default_output_dir=output_dir,
    )


def load_config(*, env_file: str | None = None) -> Config:
    path = _expand_path(env_file or os.environ.get("VERDANTFLARE_VIDEO_ENV_FILE", DEFAULT_CONFIG_FILE))
    if not path.exists():
        raise ConfigError(f"configuration file does not exist: {path}")
    return build_config(read_env_file(path), config_file=path)


def _download(url: str, destination: Path, *, max_bytes: int, allowed_hosts: set[str]) -> None:
    parsed = urllib.parse.urlparse(url)
    normalized_hosts = {host.lower().rstrip(".") for host in allowed_hosts}
    if parsed.scheme != "https" or parsed.username or parsed.password or parsed.fragment or (parsed.hostname or "").lower().rstrip(".") not in normalized_hosts:
        raise ConfigError("download host is not allowed")
    request = urllib.request.Request(url, headers={"User-Agent": "verdantflare-video/0.1"})
    opener = urllib.request.build_opener(_ValidatedRedirectHandler(allowed_hosts, max_redirections=3))
    with opener.open(request, timeout=30) as response:
        final_host = urllib.parse.urlparse(response.geturl()).hostname
        if (final_host or "").lower().rstrip(".") not in normalized_hosts:
            raise ConfigError("download redirected to an untrusted host")
        total = 0
        with destination.open("wb") as output:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise ConfigError("download exceeds size limit")
                output.write(chunk)
            output.flush()
            os.fsync(output.fileno())


def ensure_mc(config: Config) -> Path:
    if TEST_MODE and config.mc_path.exists():
        return config.mc_path
    arch = platform.machine()
    if arch not in MC_ASSETS:
        raise ConfigError(f"unsupported macOS architecture: {arch}")
    url, expected_sha = MC_ASSETS[arch]
    target = config.mc_path
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(target.parent, 0o700)
    if target.exists():
        digest = hashlib.sha256(target.read_bytes()).hexdigest()
        if digest == expected_sha:
            os.chmod(target, 0o700)
            _validate_mc_binary(target, arch)
            return target
        target.unlink()
    with tempfile.NamedTemporaryFile(prefix="mc.", dir=target.parent, delete=False) as temp:
        temporary = Path(temp.name)
    try:
        _download(url, temporary, max_bytes=128 * 1024 * 1024, allowed_hosts=MC_ALLOWED_REDIRECT_HOSTS | {"github.com"})
        digest = hashlib.sha256(temporary.read_bytes()).hexdigest()
        if digest != expected_sha:
            raise ConfigError("mc SHA-256 verification failed")
        os.chmod(temporary, 0o700)
        _validate_mc_binary(temporary, arch)
        os.replace(temporary, target)
        os.chmod(target, 0o700)
        return target
    finally:
        if temporary.exists():
            temporary.unlink()


def _validate_mc_binary(path: Path, arch: str) -> None:
    """Check that a pinned binary is executable and matches this host."""
    try:
        version = subprocess.run([str(path), "--version"], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError) as exc:
        raise ConfigError("mc failed version check") from exc
    if version.returncode != 0:
        raise ConfigError("mc failed version check")
    if shutil.which("file"):
        try:
            file_info = subprocess.run(["file", str(path)], capture_output=True, text=True, timeout=10)
        except (OSError, subprocess.SubprocessError) as exc:
            raise ConfigError("mc architecture check failed") from exc
        expected_arch = "arm64" if arch == "arm64" else "x86_64"
        if expected_arch not in file_info.stdout:
            raise ConfigError("mc architecture mismatch")


def _download_bootstrap(url: str) -> str:
    if len(url.encode("utf-8")) > 4096 or any(ord(char) < 0x20 or ord(char) == 0x7F for char in url):
        raise ConfigError("bootstrap URL is invalid")
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or parsed.username or parsed.password or parsed.fragment:
        raise ConfigError("bootstrap URL must be HTTPS without credentials or fragment")
    try:
        if parsed.port not in (None, 443):
            raise ConfigError("bootstrap URL must use HTTPS port 443")
    except ValueError as exc:
        raise ConfigError("bootstrap URL has an invalid port") from exc
    if parsed.hostname not in CONFIG_ALLOWED_HOSTS:
        raise ConfigError("bootstrap URL host is not allowed")
    request = urllib.request.Request(url, headers={"User-Agent": "verdantflare-video/0.1"})
    opener = urllib.request.build_opener(
        _ValidatedRedirectHandler({parsed.hostname}, same_host=parsed.hostname, max_redirections=1)
    )
    with opener.open(request, timeout=30) as response:
        final = urllib.parse.urlparse(response.geturl())
        if final.hostname != parsed.hostname or final.scheme != "https":
            raise ConfigError("bootstrap URL redirected to another host")
        data = response.read(32 * 1024 + 1)
    if len(data) > 32 * 1024:
        raise ConfigError("bootstrap configuration exceeds 32 KiB")
    return data.decode("utf-8")


def _api_models(config: Config) -> None:
    request = urllib.request.Request(
        f"{config.api_base_url}/models",
        headers={"Authorization": f"Bearer {config.api_key}", "Accept": "application/json"},
    )
    try:
        api_host = urllib.parse.urlparse(config.api_base_url).hostname or ""
        opener = urllib.request.build_opener(_ValidatedRedirectHandler({api_host}, same_host=api_host, max_redirections=2))
        with opener.open(request, timeout=30) as response:
            body = response.read(1024 * 1024)
    except urllib.error.HTTPError as exc:
        raise ConfigError(f"API connectivity check failed with HTTP {exc.code}") from exc
    except OSError as exc:
        raise ConfigError("API connectivity check failed") from exc
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise ConfigError("API connectivity check returned invalid JSON") from exc
    text = json.dumps(payload, ensure_ascii=False)
    if "verdantflare-sd2" not in text:
        raise ConfigError("public model verdantflare-sd2 is not available for this API key")


def _write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", delete=False) as temp:
            temporary = Path(temp.name)
            temp.write(text)
            temp.flush()
            os.fsync(temp.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _render_env(values: Mapping[str, str]) -> str:
    return "".join(f"{key}={values[key]}\n" for key in sorted(values))


def install() -> int:
    try:
        if not sys.stdin.isatty() and not Path("/dev/tty").exists():
            raise ConfigError("a connected TTY is required to enter the one-time bootstrap URL")
        try:
            url = getpass.getpass("Paste one-time VerdantFlare configuration URL: ")
        except (EOFError, OSError) as exc:
            raise ConfigError("could not read bootstrap URL") from exc
        candidate_values = parse_env_text(_download_bootstrap(url.strip()))
        candidate = build_config(candidate_values)
        candidate.ensure_dirs()
        ensure_mc(candidate)
        if not TEST_MODE:
            _api_models(candidate)
        _write_atomic(candidate.config_file, _render_env(candidate_values))
        print("Configuration installed successfully.")
        return 0
    except (ConfigError, OSError, urllib.error.URLError) as exc:
        message = re.sub(r"https?://[^\s]+", "[redacted-url]", str(exc))[:500]
        print(f"Configuration failed: {message}", file=sys.stderr)
        return 1


def check() -> int:
    try:
        config = load_config()
        config.ensure_dirs()
        ensure_mc(config)
        print(json.dumps(config.redacted(), ensure_ascii=False, sort_keys=True))
        return 0
    except (ConfigError, OSError, urllib.error.URLError) as exc:
        print(f"Configuration invalid: {exc}", file=sys.stderr)
        return 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("install", "check"))
    args = parser.parse_args()
    return install() if args.command == "install" else check()


if __name__ == "__main__":
    raise SystemExit(main())
