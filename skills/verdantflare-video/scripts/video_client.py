#!/usr/bin/env python3
"""Generate, resume, list, and download VerdantFlare Video tasks."""

from __future__ import annotations

import argparse
import datetime as dt
import email.utils
import hashlib
import hmac
import ipaddress
import json
import os
import random
import re
import shutil
import signal
import socket
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from config import Config, ConfigError, ensure_mc, load_config
from task_store import (
    BusyTaskError,
    list_submissions,
    list_tasks,
    load_submission,
    load_task,
    save_submission,
    save_task,
    submission_lock,
    task_lock,
    task_path,
)


MAX_IMAGE_COUNT = 9
MAX_AUDIO_COUNT = 1
MAX_VIDEO_COUNT = 3
MAX_IMAGE_BYTES = 3 * 1024 * 1024
MAX_AUDIO_BYTES = 6 * 1024 * 1024
MAX_VIDEO_BYTES = 12 * 1024 * 1024
MAX_LOCAL_TOTAL_BYTES = 48 * 1024 * 1024
MAX_DOWNLOAD_BYTES = 1024 * 1024 * 1024
DOWNLOAD_TIMEOUT_SECONDS = 10 * 60
MEDIA_READINESS_DELAYS_SECONDS = (0, 1, 2, 4)
UPLOAD_HARD_TTL_SECONDS = 7 * 24 * 60 * 60
ALLOWED_RATIOS = {"16:9", "9:16", "1:1", "4:3", "3:4"}
EXTENSIONS = {
    "image": {".jpg", ".jpeg", ".png", ".webp"},
    "audio": {".mp3", ".wav", ".m4a"},
    "video": {".mp4", ".mov", ".webm"},
}
MIME_TYPES = {
    "image": {"image/jpeg", "image/png", "image/webp"},
    "audio": {"audio/mpeg", "audio/wav", "audio/x-wav", "audio/mp4"},
    "video": {"video/mp4", "video/quicktime", "video/webm"},
}


class ClientError(RuntimeError):
    """A user-safe client error."""


class PollInterrupted(Exception):
    """Raised by a signal handler so the task can be persisted as paused."""


class ProtocolError(ClientError):
    """Raised when the API violates the documented response contract."""


class ApiError(ClientError):
    def __init__(self, status: int, message: str, body: Any = None, retry_after: float | None = None):
        super().__init__(message)
        self.status = status
        self.body = body
        self.retry_after = retry_after


@dataclass
class Media:
    kind: str
    source: str
    url: str | None = None
    size: int = 0
    fingerprint: tuple[int, int, int, int] | None = None
    object_key: str | None = None


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _utc_after(seconds: int) -> str:
    value = dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=seconds)
    return value.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_utc(value: Any) -> dt.datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(dt.timezone.utc)


def redact_message(message: str) -> str:
    message = re.sub(r"Bearer\s+\S+", "Bearer [redacted]", message, flags=re.IGNORECASE)
    message = re.sub(r"(?i)(api[_-]?key|access[_-]?key|secret[_-]?key|authorization)=?\s*[^\s,;]+", r"\1=[redacted]", message)
    message = re.sub(r"(https?://)[^/@\s]+@", r"\1[redacted]@", message)
    message = re.sub(r"(https?://[^\s?]+)\?[^\s]+", r"\1?[redacted]", message)
    return message[:500]


def _public_host(host: str) -> bool:
    if os.environ.get("VERDANTFLARE_VIDEO_TEST_MODE") == "1" and host in {"localhost", "127.0.0.1", "::1"}:
        return True
    try:
        addresses = {item[4][0] for item in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)}
    except OSError as exc:
        raise ClientError(f"cannot resolve media host {host}") from exc
    for address in addresses:
        parsed = ipaddress.ip_address(address)
        if not parsed.is_global:
            raise ClientError(f"media host resolves to a private address: {host}")
    return True


def validate_external_url(value: str, *, result=False) -> str:
    if any(ord(char) < 0x20 or ord(char) == 0x7F for char in value):
        raise ClientError("media URL contains control characters")
    parsed = urllib.parse.urlparse(value)
    if parsed.scheme != "https" or parsed.username or parsed.password or parsed.fragment:
        if not (os.environ.get("VERDANTFLARE_VIDEO_TEST_MODE") == "1" and parsed.hostname in {"localhost", "127.0.0.1"}):
            raise ClientError("media URL must be HTTPS without credentials or fragment")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ClientError("media URL has an invalid port") from exc
    if port not in (None, 443) and os.environ.get("VERDANTFLARE_VIDEO_TEST_MODE") != "1":
        raise ClientError("media URL must use HTTPS port 443")
    host = (parsed.hostname or "").lower().rstrip(".")
    if not host:
        raise ClientError("media URL host is required")
    _public_host(host)
    return value


def _media_magic(kind: str, header: bytes) -> bool:
    if kind == "image":
        return header.startswith(b"\xff\xd8\xff") or header.startswith(b"\x89PNG\r\n\x1a\n") or header.startswith(b"RIFF") and header[8:12] == b"WEBP"
    if kind == "audio":
        return header.startswith(b"ID3") or header.startswith(b"RIFF") and header[8:12] == b"WAVE" or header.startswith(b"\xff\xfb")
    if kind == "video":
        return len(header) >= 12 and header[4:8] == b"ftyp" or header.startswith(b"\x1aE\xdf\xa3")
    return False


def validate_local_media(kind: str, source: str) -> Media:
    path = Path(source).expanduser()
    if not path.exists() or not path.is_file():
        raise ClientError(f"media file does not exist or is not a regular file: {source}")
    if path.is_symlink():
        raise ClientError(f"media symlinks are not accepted: {source}")
    extension = path.suffix.lower()
    if extension not in EXTENSIONS[kind]:
        raise ClientError(f"unsupported {kind} extension: {source}")
    initial_stat = path.stat()
    size = initial_stat.st_size
    limits = {"image": MAX_IMAGE_BYTES, "audio": MAX_AUDIO_BYTES, "video": MAX_VIDEO_BYTES}
    if size <= 0 or size > limits[kind]:
        raise ClientError(f"{kind} file size is outside the v1 limit: {source}")
    with path.open("rb") as stream:
        header = stream.read(512)
    if not _media_magic(kind, header):
        raise ClientError(f"{kind} file content does not match its extension: {source}")
    final_stat = path.stat()
    fingerprint = (initial_stat.st_dev, initial_stat.st_ino, initial_stat.st_size, initial_stat.st_mtime_ns)
    final_fingerprint = (final_stat.st_dev, final_stat.st_ino, final_stat.st_size, final_stat.st_mtime_ns)
    if final_fingerprint != fingerprint:
        raise ClientError(f"media file changed during validation: {source}")
    return Media(kind=kind, source=str(path.resolve()), size=size, fingerprint=fingerprint)


def collect_media(args: argparse.Namespace) -> list[Media]:
    media: list[Media] = []
    for kind in ("image", "video", "audio"):
        local_values = getattr(args, kind, []) or []
        url_values = getattr(args, f"{kind}_url", []) or []
        for source in local_values:
            media.append(validate_local_media(kind, source))
        for url in url_values:
            media.append(Media(kind=kind, source=validate_external_url(url), url=url))
    counts = {kind: sum(item.kind == kind for item in media) for kind in EXTENSIONS}
    if counts["image"] > MAX_IMAGE_COUNT:
        raise ClientError("at most 9 image references are supported")
    if counts["audio"] > MAX_AUDIO_COUNT:
        raise ClientError("v1 supports at most 1 audio reference")
    if counts["video"] > MAX_VIDEO_COUNT:
        raise ClientError("v1 supports at most 3 video references")
    if sum(item.size for item in media) > MAX_LOCAL_TOTAL_BYTES:
        raise ClientError("local media exceeds the 48 MiB request limit")
    return media


def build_payload(prompt: str, media_urls: Iterable[Media], *, duration: int, ratio: str, generate_audio: bool, watermark: bool) -> dict[str, Any]:
    prompt = prompt.strip()
    if not prompt:
        raise ClientError("prompt must not be empty")
    if not 1 <= duration <= 15:
        raise ClientError("duration must be between 1 and 15 seconds")
    if ratio not in ALLOWED_RATIOS:
        raise ClientError(f"unsupported ratio: {ratio}")
    content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
    for media in media_urls:
        if not media.url:
            raise ClientError("media must be uploaded before request construction")
        key = f"{media.kind}_url"
        content.append({"type": key, key: {"url": media.url}})
    return {
        "model": "verdantflare-sd2",
        "messages": [{"role": "user", "content": content}],
        "duration": duration,
        "ratio": ratio,
        "generate_audio": generate_audio,
        "watermark": watermark,
    }


def _object_url(config: Config, key: str) -> str:
    return config.s3_public_base_url.rstrip("/") + "/" + "/".join(urllib.parse.quote(part, safe="") for part in key.split("/"))


def _ownership_proof(config: Config, bucket: str, key: str) -> str:
    message = f"verdantflare-video-owned\0{bucket}\0{key}".encode("utf-8")
    return hmac.new(config.s3_secret_key.encode("utf-8"), message, hashlib.sha256).hexdigest()


def _owned_object_record(config: Config, key: str) -> dict[str, Any]:
    return {
        "bucket": config.s3_bucket,
        "key": key,
        "ownership_proof": _ownership_proof(config, config.s3_bucket, key),
        "planned_at": utc_now(),
        "uploaded_at": None,
        "delete_after": _utc_after(UPLOAD_HARD_TTL_SECONDS),
        "upload_state": "PENDING",
        "cleanup_state": "PENDING",
        "cleaned_at": None,
    }


def _is_owned_object(config: Config, item: Any) -> bool:
    if not isinstance(item, dict):
        return False
    bucket = item.get("bucket")
    key = item.get("key")
    proof = item.get("ownership_proof")
    if bucket != config.s3_bucket or not isinstance(key, str) or not isinstance(proof, str):
        return False
    prefix = re.escape(config.object_prefix.rstrip("/"))
    if not re.fullmatch(prefix + r"/\d{4}-\d{2}-\d{2}/[0-9a-f]{32}\.[a-z0-9]+", key):
        return False
    expected = _ownership_proof(config, bucket, key)
    return hmac.compare_digest(proof, expected)


def _lifecycle_rule_values(rule: Any) -> tuple[str, int | None, str]:
    if not isinstance(rule, dict):
        return "", None, ""
    status = str(rule.get("Status", rule.get("status", ""))).lower()
    expiration = rule.get("Expiration", rule.get("expiration"))
    days: Any = None
    if isinstance(expiration, dict):
        days = expiration.get("Days", expiration.get("days"))
    if days is None:
        days = rule.get("ExpirationDays", rule.get("expiration_days"))
    if isinstance(days, bool) or not isinstance(days, int):
        days = None
    prefix = rule.get("Prefix", rule.get("prefix", ""))
    filter_value = rule.get("Filter", rule.get("filter"))
    if not prefix and isinstance(filter_value, dict):
        prefix = filter_value.get("Prefix", filter_value.get("prefix", ""))
    return status, days, str(prefix or "")


def _iter_lifecycle_rules(value: Any):
    if isinstance(value, list):
        for item in value:
            yield from _iter_lifecycle_rules(item)
        return
    if not isinstance(value, dict):
        return
    status, days, _prefix = _lifecycle_rule_values(value)
    if status and days is not None:
        yield value
    for key, nested in value.items():
        if str(key).lower() in {"rule", "rules", "lifecycle", "config", "configuration"}:
            yield from _iter_lifecycle_rules(nested)


def _lifecycle_documents(stdout: str) -> list[Any]:
    try:
        return [json.loads(stdout)]
    except json.JSONDecodeError:
        pass
    documents: list[Any] = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            documents.append(json.loads(line))
        except json.JSONDecodeError:
            return []
    return documents


def verify_bucket_lifecycle(config: Config) -> int:
    """Read and verify an existing per-installation hard-TTL rule; never mutate it."""
    mc = ensure_mc(config)
    endpoint = urllib.parse.urlparse(config.s3_endpoint)
    credentials = f"{urllib.parse.quote(config.s3_access_key, safe='')}:{urllib.parse.quote(config.s3_secret_key, safe='')}"
    host_url = urllib.parse.urlunparse(endpoint._replace(netloc=f"{credentials}@{endpoint.netloc}"))
    with tempfile.TemporaryDirectory(prefix="vf-mc-") as mc_config:
        env = os.environ.copy()
        env["MC_CONFIG_DIR"] = mc_config
        env["MC_HOST_vfvideo"] = host_url
        try:
            result = subprocess.run(
                [str(mc), "ilm", "rule", "ls", f"vfvideo/{config.s3_bucket}", "--expiry", "--json"],
                env=env,
                capture_output=True,
                text=True,
                timeout=60,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise ClientError("temporary input lifecycle could not be verified; no media was uploaded or submitted") from exc
    expected_prefix = config.object_prefix.rstrip("/")
    if result.returncode == 0:
        for document in _lifecycle_documents(result.stdout):
            for rule in _iter_lifecycle_rules(document):
                status, days, prefix = _lifecycle_rule_values(rule)
                if status == "enabled" and days == 7 and prefix.rstrip("/") == expected_prefix:
                    return days
    raise ClientError(
        "temporary input bucket requires an enabled lifecycle rule for this installation's upload prefix "
        "with expiration of exactly 7 days; ask the administrator to configure it; no media was uploaded or submitted"
    )


def _verify_uploaded_media(media: Media) -> None:
    if not media.url:
        raise ClientError("uploaded media URL is missing")
    url = validate_external_url(media.url)
    opener = urllib.request.build_opener(_ResultRedirectHandler())
    last_status = 0
    for delay in MEDIA_READINESS_DELAYS_SECONDS:
        if delay:
            time.sleep(delay)
        request = urllib.request.Request(url, headers={"Accept": "*/*", "Range": "bytes=0-511"})
        try:
            with opener.open(request, timeout=30) as response:
                last_status = response.status
                if last_status not in (200, 206):
                    continue
                content_type = (response.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
                if content_type and content_type not in MIME_TYPES[media.kind] and content_type != "application/octet-stream":
                    raise ClientError(f"uploaded {media.kind} response has an unexpected content type")
                header = response.read(512)
                if not _media_magic(media.kind, header):
                    raise ClientError(f"uploaded {media.kind} is not readable as the expected media type")
                return
        except urllib.error.HTTPError as exc:
            last_status = exc.code
            if exc.code not in (403, 404, 408, 409, 425, 429, 500, 502, 503, 504):
                break
        except (urllib.error.URLError, TimeoutError, OSError):
            last_status = 0
    detail = f" (HTTP {last_status})" if last_status else ""
    raise ClientError(f"uploaded media is not publicly readable before video submission{detail}; no video task was submitted")


def _media_upload_error(config: Config, stderr: str) -> str:
    detail = stderr.lower()
    if "nosuchbucket" in detail or "bucket does not exist" in detail or "specified bucket" in detail:
        return (
            f"media upload failed before video submission: bucket '{config.s3_bucket}' does not exist "
            "or has not been provisioned; ask the administrator to update the bootstrap configuration"
        )
    if "accessdenied" in detail or "access denied" in detail or "forbidden" in detail:
        return (
            f"media upload failed before video submission: access to bucket '{config.s3_bucket}' was denied; "
            "ask the administrator to provision access for this installation"
        )
    return f"media upload failed before video submission: {redact_message(stderr or 'object storage request failed')}"


def upload_media(config: Config, media: Media, *, record_upload=None) -> Media:
    if media.url:
        return media
    if Path(media.source).is_symlink():
        raise ClientError(f"media symlinks are not accepted: {media.source}")
    current = Path(media.source).stat()
    current_fingerprint = (current.st_dev, current.st_ino, current.st_size, current.st_mtime_ns)
    if media.fingerprint and current_fingerprint != media.fingerprint:
        raise ClientError(f"media file changed before upload: {media.source}")
    mc = ensure_mc(config)
    date = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")
    extension = Path(media.source).suffix.lower().lstrip(".")
    key = f"{config.object_prefix}/{date}/{uuid.uuid4().hex}.{extension}"
    endpoint = urllib.parse.urlparse(config.s3_endpoint)
    credentials = f"{urllib.parse.quote(config.s3_access_key, safe='')}:{urllib.parse.quote(config.s3_secret_key, safe='')}"
    host_url = urllib.parse.urlunparse(endpoint._replace(netloc=f"{credentials}@{endpoint.netloc}"))
    owned_object = _owned_object_record(config, key)
    if record_upload is None:
        raise ClientError("media upload requires a durable ownership manifest")
    record_upload(owned_object)
    with tempfile.TemporaryDirectory(prefix="vf-mc-") as mc_config:
        env = os.environ.copy()
        env["MC_CONFIG_DIR"] = mc_config
        env["MC_HOST_vfvideo"] = host_url
        try:
            result = subprocess.run(
                [str(mc), "cp", "--quiet", media.source, f"vfvideo/{config.s3_bucket}/{key}"],
                env=env,
                capture_output=True,
                text=True,
                timeout=300,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise ClientError("media upload failed") from exc
    if result.returncode != 0:
        raise ClientError(_media_upload_error(config, result.stderr or "mc failed"))
    owned_object["upload_state"] = "UPLOADED"
    owned_object["uploaded_at"] = utc_now()
    record_upload(owned_object)
    uploaded = Media(kind=media.kind, source=media.source, url=_object_url(config, key), size=media.size, object_key=key)
    _verify_uploaded_media(uploaded)
    return uploaded


def _delete_owned_object(config: Config, item: dict[str, Any]) -> bool:
    if not _is_owned_object(config, item):
        return False
    try:
        mc = ensure_mc(config)
    except (ConfigError, OSError):
        return False
    endpoint = urllib.parse.urlparse(config.s3_endpoint)
    credentials = f"{urllib.parse.quote(config.s3_access_key, safe='')}:{urllib.parse.quote(config.s3_secret_key, safe='')}"
    host_url = urllib.parse.urlunparse(endpoint._replace(netloc=f"{credentials}@{endpoint.netloc}"))
    target = f"vfvideo/{item['bucket']}/{item['key']}"
    try:
        with tempfile.TemporaryDirectory(prefix="vf-mc-") as mc_config:
            env = os.environ.copy()
            env["MC_CONFIG_DIR"] = mc_config
            env["MC_HOST_vfvideo"] = host_url
            result = subprocess.run(
                [str(mc), "rm", "--quiet", "--force", target],
                env=env,
                capture_output=True,
                text=True,
                timeout=120,
            )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def cleanup_record_uploads(config: Config, record: dict[str, Any], *, force: bool = False) -> bool:
    cleanup = record.get("cleanup")
    if not isinstance(cleanup, dict) or not isinstance(cleanup.get("objects"), list):
        return False
    now = dt.datetime.now(dt.timezone.utc)
    changed = False
    for item in cleanup["objects"]:
        if not isinstance(item, dict) or item.get("cleanup_state") == "CLEANED":
            continue
        deadline = _parse_utc(item.get("delete_after"))
        if not force and (deadline is None or now < deadline):
            continue
        item["last_cleanup_at"] = utc_now()
        if _delete_owned_object(config, item):
            identity = item.get("ownership_proof")
            item.clear()
            item.update({"object_identity": identity, "cleanup_state": "CLEANED", "cleaned_at": utc_now()})
        else:
            item["cleanup_state"] = "PENDING"
            item["last_cleanup_error"] = "object_cleanup_failed"
        changed = True
    if changed:
        cleanup["updated_at"] = utc_now()
        record["updated_at"] = utc_now()
    return changed


def cleanup_expired_uploads(config: Config) -> None:
    for submission in list_submissions(config):
        request_id = submission["client_request_id"]
        try:
            with submission_lock(config, request_id):
                current = load_submission(config, request_id)
                force = current.get("submission_state") == "REJECTED"
                if cleanup_record_uploads(config, current, force=force):
                    save_submission(config, current)
        except (BusyTaskError, OSError, ValueError):
            continue
    for task in list_tasks(config):
        task_id = task["task_id"]
        try:
            with task_lock(config, task_id):
                current = load_task(config, task_id)
                force = current.get("status") in {"completed", "failed", "failure"}
                if cleanup_record_uploads(config, current, force=force):
                    save_task(config, current)
        except (BusyTaskError, OSError, ValueError):
            continue


def _json_body(data: bytes) -> Any:
    try:
        return json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None


class _ApiRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Allow same-origin read redirects; mutation redirects require recovery."""

    def __init__(self, host: str):
        super().__init__()
        self.host = host.lower().rstrip(".")
        self.max_redirections = 2

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if req.get_method().upper() not in {"GET", "HEAD"}:
            raise ClientError("API mutation redirected; recover the submission by client request id")
        parsed = urllib.parse.urlparse(newurl)
        host = (parsed.hostname or "").lower().rstrip(".")
        is_test = os.environ.get("VERDANTFLARE_VIDEO_TEST_MODE") == "1" and host in {"localhost", "127.0.0.1", "::1"}
        try:
            port = parsed.port
        except ValueError as exc:
            raise ClientError("API redirected to an invalid URL") from exc
        if host != self.host or (parsed.scheme != "https" and not is_test) or (port not in (None, 443) and not is_test) or parsed.username or parsed.password or parsed.fragment:
            raise ClientError("API redirected to an untrusted host")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class _ResultRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Reject redirects from the stable VerdantFlare result proxy."""

    def __init__(self, config: Config | None = None, task_id: str | None = None):
        super().__init__()
        self.max_redirections = 3
        self.config = config
        self.task_id = task_id

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ClientError("result download redirected; the stable API content proxy must respond directly")


def _api_opener(config: Config) -> urllib.request.OpenerDirector:
    host = urllib.parse.urlparse(config.api_base_url).hostname or ""
    return urllib.request.build_opener(_ApiRedirectHandler(host))


def _retry_after(value: str | None) -> float | None:
    if not value:
        return None
    try:
        seconds = float(value)
        return max(0.0, min(120.0, seconds))
    except ValueError:
        pass
    try:
        date = email.utils.parsedate_to_datetime(value)
        if date is None:
            return None
        if date.tzinfo is None:
            date = date.replace(tzinfo=dt.timezone.utc)
        return max(0.0, min(120.0, (date - dt.datetime.now(dt.timezone.utc)).total_seconds()))
    except (TypeError, ValueError, OverflowError):
        return None


def api_request(config: Config, method: str, path: str, *, payload: dict[str, Any] | None = None, request_id: str | None = None) -> Any:
    body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {"Authorization": f"Bearer {config.api_key}", "Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    if request_id:
        headers["Idempotency-Key"] = request_id
        headers["X-Request-ID"] = request_id
    request = urllib.request.Request(config.api_base_url.rstrip("/") + path, data=body, headers=headers, method=method)
    try:
        with _api_opener(config).open(request, timeout=60) as response:
            return _json_body(response.read(4 * 1024 * 1024))
    except urllib.error.HTTPError as exc:
        response_body = exc.read(1024 * 1024)
        raise ApiError(exc.code, f"API returned HTTP {exc.code}", _json_body(response_body), _retry_after(exc.headers.get("Retry-After"))) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ApiError(0, "API request failed") from exc


def _error_summary(body: Any) -> str:
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict):
            code = str(error.get("code", "api_error"))
            message = str(error.get("message", "request failed"))
            return redact_message(f"{code}: {message}")
        for key in ("message", "msg", "error"):
            if body.get(key):
                return redact_message(str(body[key]))
    return "request failed"


def _task_id_from_create(body: Any) -> str:
    if not isinstance(body, dict):
        raise ClientError("create response is not JSON object")
    task_id = body.get("id")
    if not isinstance(task_id, str) or not task_id:
        raise ClientError("create response does not contain a public task id")
    alternate = body.get("task_id")
    if alternate is not None and alternate != task_id:
        raise ClientError("create response id and task_id do not match")
    return task_id


def _api_error_code(body: Any) -> str:
    if not isinstance(body, dict):
        return ""
    error = body.get("error")
    if isinstance(error, dict):
        return str(error.get("code", ""))
    return str(body.get("code", ""))


def _query_submission_with_retries(config: Config, client_request_id: str, *, retry_limit: int | None = None) -> dict[str, Any]:
    failures = 0
    allowed_retries = config.retry_limit if retry_limit is None else retry_limit
    path = f"/video-submissions/{urllib.parse.quote(client_request_id, safe='')}"
    while True:
        try:
            body = api_request(config, "GET", path)
            if not isinstance(body, dict):
                raise ProtocolError("submission query response is not a JSON object")
            return body
        except ApiError as exc:
            if exc.status in (401, 403):
                raise ClientError("API authorization failed; reinstall configuration") from exc
            if exc.status == 404:
                raise ClientError("video submission was not found") from exc
            if exc.status not in (0, 408, 429) and not 500 <= exc.status < 600:
                raise ClientError(_error_summary(exc.body)) from exc
            failures += 1
            if failures > allowed_retries:
                raise ClientError("temporary submission query failures exceeded retry limit") from exc
            delay = exc.retry_after
            if delay is None:
                delay = min(120, max(1, config.poll_interval * (2 ** min(failures - 1, 5))) + random.random())
            time.sleep(delay)


def _apply_submission_view(submission: dict[str, Any], response: dict[str, Any]) -> str:
    request_id = response.get("client_request_id")
    if request_id != submission["client_request_id"]:
        raise ProtocolError("submission query returned a different client request id")
    state = str(response.get("submission_state", "")).upper()
    if state not in {"PREPARED", "SENDING", "CONFIRMED", "REJECTED", "UNKNOWN"}:
        raise ProtocolError("submission query returned an unknown state")
    public_task_id = response.get("public_task_id")
    if state == "CONFIRMED" and (not isinstance(public_task_id, str) or not public_task_id):
        raise ProtocolError("confirmed submission did not contain a public task id")
    submission["submission_state"] = state
    submission["updated_at"] = utc_now()
    submission["remote"] = {
        "billing_state": response.get("billing_state"),
        "created_at": response.get("created_at"),
        "updated_at": response.get("updated_at"),
    }
    if state == "CONFIRMED":
        submission["task_id"] = public_task_id
        submission["last_error"] = None
    else:
        submission.pop("task_id", None)
        code = response.get("error_code")
        message = response.get("error_message")
        submission["last_error"] = (
            {"code": str(code or "submission_unresolved"), "message": redact_message(str(message or "submission requires recovery"))}
            if state in {"REJECTED", "UNKNOWN"}
            else None
        )
    return state


def _planned_path(output: Path, task_id: str, explicit_file: bool) -> Path:
    if explicit_file:
        return output
    safe_id = task_id if re.fullmatch(r"[A-Za-z0-9_-]{1,128}", task_id) else hashlib.sha256(task_id.encode()).hexdigest()
    candidate = output / f"{safe_id}.mp4"
    index = 0
    while candidate.exists():
        index += 1
        candidate = output / f"{safe_id}-{index}.mp4"
    return candidate


def _prepare_output(config: Config, requested: str | None) -> tuple[Path, bool]:
    value = Path(requested).expanduser() if requested else (config.default_output_dir or Path.cwd())
    explicit_file = requested is not None and value.suffix.lower() == ".mp4"
    directory = value.parent if explicit_file else value
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    if not directory.is_dir() or not os.access(directory, os.W_OK):
        raise ClientError(f"output directory is not writable: {directory}")
    output = value.resolve() if explicit_file else directory.resolve()
    if explicit_file and output.exists():
        raise ClientError(f"refusing to overwrite existing output: {output}")
    return output, explicit_file


def _new_task(task_id: str, request_id: str, payload: dict[str, Any], output: Path, explicit_file: bool, response: dict[str, Any]) -> dict[str, Any]:
    output_dir = output.parent if explicit_file else output
    planned = output if explicit_file else _planned_path(output, task_id, False)
    return {
        "schema_version": 1,
        "task_id": task_id,
        "client_request_id": request_id,
        "model": "verdantflare-sd2",
        "status": str(response.get("status", "queued")).lower(),
        "progress": response.get("progress") if isinstance(response.get("progress"), int) and not isinstance(response.get("progress"), bool) else None,
        "tracking_state": "ACTIVE",
        "created_at": utc_now(),
        "updated_at": utc_now(),
        "last_checked_at": None,
        "request": {
            "prompt_preview": payload["messages"][0]["content"][0]["text"][:120],
            "ratio": payload["ratio"],
            "duration": payload["duration"],
            "generate_audio": payload["generate_audio"],
            "watermark": payload["watermark"],
            "media_counts": {kind: sum(item.get("type") == f"{kind}_url" for item in payload["messages"][0]["content"]) for kind in ("image", "audio", "video")},
        },
        "output": {"directory": str(output_dir), "planned_path": str(planned), "overwrite": False},
        "result": {"url": None, "url_redacted": None, "local_path": None, "download_state": "PENDING", "metadata": None},
        "last_error": None,
    }


def _promote_confirmed_submission(config: Config, submission: dict[str, Any], response: dict[str, Any]) -> dict[str, Any]:
    task_id = str(submission.get("task_id", ""))
    if not task_id:
        raise ProtocolError("confirmed submission does not contain a task id")
    try:
        existing = load_task(config, task_id)
        if existing.get("client_request_id") != submission["client_request_id"]:
            raise ProtocolError("task id belongs to another local submission")
        return existing
    except ValueError:
        pass
    output_spec = submission.get("output") if isinstance(submission.get("output"), dict) else {}
    requested = output_spec.get("requested") or submission.get("output_directory") or config.default_output_dir or Path.cwd()
    output = Path(requested).expanduser().resolve()
    explicit_file = bool(output_spec.get("explicit_file"))
    request_summary = submission.get("request") if isinstance(submission.get("request"), dict) else {}
    payload = {
        "messages": [{"content": [{"type": "text", "text": "[recovered submission]"}]}],
        "ratio": request_summary.get("ratio", "16:9"),
        "duration": request_summary.get("duration", 10),
        "generate_audio": bool(request_summary.get("generate_audio", True)),
        "watermark": bool(request_summary.get("watermark", False)),
    }
    task_response = {"status": str(response.get("task_status", "queued")).lower()}
    task = _new_task(task_id, submission["client_request_id"], payload, output, explicit_file, task_response)
    task["request"] = dict(request_summary)
    task["cleanup"] = submission.get("cleanup", {"objects": []})
    record_path = save_task(config, task)
    task["record_path"] = str(record_path)
    save_task(config, task)
    submission["cleanup"] = {"objects": [], "transferred_to_task_id": task_id, "updated_at": utc_now()}
    submission["updated_at"] = utc_now()
    save_submission(config, submission)
    return task


def recover_submission_record(
    config: Config, submission: dict[str, Any], *, retry_limit: int | None = None
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    try:
        response = _query_submission_with_retries(config, submission["client_request_id"], retry_limit=retry_limit)
        state = _apply_submission_view(submission, response)
    except ClientError as exc:
        submission["submission_state"] = "UNKNOWN"
        submission["updated_at"] = utc_now()
        submission["last_error"] = {"code": "recovery_failed", "message": redact_message(str(exc))}
        save_submission(config, submission)
        return submission, None
    save_submission(config, submission)
    if state == "REJECTED":
        if cleanup_record_uploads(config, submission, force=True):
            save_submission(config, submission)
        return submission, None
    if state != "CONFIRMED":
        return submission, None
    task = _promote_confirmed_submission(config, submission, response)
    return submission, task


def _save_error(task: dict[str, Any], code: str, message: str) -> None:
    task["last_error"] = {"code": code, "message": redact_message(message)}
    task["updated_at"] = utc_now()


def _is_api_content_url(config: Config, task_id: str, url: str) -> bool:
    api = urllib.parse.urlparse(config.api_base_url)
    candidate = urllib.parse.urlparse(url)
    if (candidate.scheme.lower(), candidate.hostname, candidate.port) != (api.scheme.lower(), api.hostname, api.port):
        return False
    expected_path = api.path.rstrip("/") + f"/videos/{urllib.parse.quote(task_id, safe='')}/content"
    return candidate.path == expected_path and not candidate.params and not candidate.query and not candidate.fragment


def _result_request_headers(config: Config, task_id: str, url: str) -> dict[str, str]:
    headers = {"Accept": "video/mp4,application/octet-stream"}
    if _is_api_content_url(config, task_id, url):
        headers["Authorization"] = f"Bearer {config.api_key}"
    return headers


def _download_result(config: Config, task: dict[str, Any], url: str) -> str:
    planned = Path(task["output"]["planned_path"])
    if planned.is_symlink():
        raise ClientError("planned output is a symlink")
    planned.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temp_path = planned.parent / f".{planned.name}.part.{os.getpid()}"
    parsed = urllib.parse.urlparse(validate_external_url(url, result=True))
    headers = _result_request_headers(config, task["task_id"], url)
    request = urllib.request.Request(urllib.parse.urlunparse(parsed), headers=headers)
    opener = urllib.request.build_opener(_ResultRedirectHandler(config, task["task_id"]))
    deadline = time.monotonic() + DOWNLOAD_TIMEOUT_SECONDS
    try:
        with opener.open(request, timeout=60) as response:
            content_type = (response.headers.get("Content-Type") or "").lower()
            if content_type and not (content_type.startswith("video/") or content_type in {"application/octet-stream", "application/mp4"}):
                raise ClientError("result response is not a video")
            length = response.headers.get("Content-Length")
            if length:
                try:
                    if int(length) > MAX_DOWNLOAD_BYTES:
                        raise ClientError("result exceeds 1 GiB limit")
                except ValueError as exc:
                    raise ClientError("result Content-Length is invalid") from exc
            total = 0
            with temp_path.open("wb") as output:
                os.chmod(temp_path, 0o600)
                while True:
                    if time.monotonic() >= deadline:
                        raise ClientError("result download timed out")
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > MAX_DOWNLOAD_BYTES:
                        raise ClientError("result exceeds 1 GiB limit")
                    output.write(chunk)
                if total == 0:
                    raise ClientError("result file is empty")
                output.flush()
                os.fsync(output.fileno())
        _validate_downloaded_video(temp_path)
        if planned.exists() or planned.is_symlink():
            raise ClientError("refusing to overwrite existing output")
        os.replace(temp_path, planned)
        os.chmod(planned, 0o600)
        return str(planned)
    except urllib.error.HTTPError as exc:
        raise ApiError(exc.code, f"result download returned HTTP {exc.code}") from exc
    finally:
        temp_path.unlink(missing_ok=True)


def _validate_downloaded_video(path: Path) -> None:
    """Reject empty/non-video payloads and use ffprobe when available."""
    try:
        with path.open("rb") as stream:
            header = stream.read(512)
    except OSError as exc:
        raise ClientError("downloaded result cannot be read") from exc
    if not _media_magic("video", header):
        raise ClientError("downloaded result is not a recognized video")
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return
    try:
        probe = subprocess.run(
            [ffprobe, "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=index", "-of", "csv=p=0", str(path)],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ClientError("downloaded result video validation failed") from exc
    if probe.returncode != 0 or not probe.stdout.strip():
        raise ClientError("downloaded result does not contain a video stream")


def _query_with_retries(config: Config, task_id: str) -> dict[str, Any]:
    failures = 0
    not_found_failures = 0
    while True:
        try:
            body = api_request(config, "GET", f"/videos/{urllib.parse.quote(task_id, safe='')}")
            if not isinstance(body, dict):
                raise ProtocolError("query response is not a JSON object")
            return body
        except ApiError as exc:
            if exc.status in (401, 403):
                raise ClientError("API authorization failed; reinstall configuration") from exc
            if exc.status == 404:
                if not_found_failures >= 3:
                    raise ProtocolError("task was not found after repeated queries") from exc
                not_found_failures += 1
            elif exc.status in (0, 408, 429) or 500 <= exc.status < 600:
                failures += 1
            else:
                raise ClientError(_error_summary(exc.body)) from exc
            if exc.status != 404 and failures > config.retry_limit:
                raise ClientError("temporary query failures exceeded retry limit") from exc
            delay = exc.retry_after if exc.status != 404 else None
            if delay is None:
                attempts = not_found_failures if exc.status == 404 else failures
                delay = min(10 if exc.status == 404 else 120, max(1, config.poll_interval * (2 ** min(attempts - 1, 5))) + random.random())
            time.sleep(delay)


def poll_task(config: Config, task: dict[str, Any]) -> dict[str, Any]:
    task_id = task["task_id"]
    started = time.monotonic()
    previous_handlers: dict[int, Any] = {}

    def handle_signal(signum, _frame):
        raise PollInterrupted(signum)

    can_install_handlers = threading.current_thread() is threading.main_thread()
    if can_install_handlers:
        for signum in (signal.SIGINT, signal.SIGTERM):
            previous_handlers[signum] = signal.getsignal(signum)
            signal.signal(signum, handle_signal)
    try:
      while True:
        try:
            response = _query_with_retries(config, task_id)
        except ClientError as exc:
            is_protocol_error = isinstance(exc, ProtocolError)
            task["tracking_state"] = "ERROR" if is_protocol_error else "PAUSED"
            _save_error(task, "protocol_error" if is_protocol_error else "query_failed", str(exc))
            save_task(config, task)
            return task
        status = str(response.get("status", "")).lower()
        task["status"] = status
        task["last_checked_at"] = utc_now()
        task["updated_at"] = utc_now()
        progress = response.get("progress")
        if progress is not None and (isinstance(progress, bool) or not isinstance(progress, int) or not 0 <= progress <= 100):
            _save_error(task, "protocol_error", "progress must be an integer between 0 and 100")
            task["tracking_state"] = "ERROR"
            save_task(config, task)
            return task
        if progress is not None:
            task["progress"] = progress
        task["result"]["metadata"] = {
            key: response.get("metadata", {}).get(key)
            for key in ("duration", "ratio", "resolution", "framespersecond", "generate_audio")
            if isinstance(response.get("metadata"), dict) and key in response["metadata"]
        }
        if status in {"queued", "in_progress"}:
            task["tracking_state"] = "ACTIVE"
        elif status in {"failed", "failure"}:
            error = response.get("error") if isinstance(response.get("error"), dict) else {}
            _save_error(task, str(error.get("code", "task_failed")), str(error.get("message", "task failed")))
            task["tracking_state"] = "FINISHED"
            cleanup_record_uploads(config, task, force=True)
            save_task(config, task)
            return task
        elif status == "completed":
            cleanup_record_uploads(config, task, force=True)
            save_task(config, task)
            existing = task.get("result", {}).get("local_path")
            if task.get("result", {}).get("download_state") == "SUCCEEDED" and existing and Path(existing).is_file():
                task["tracking_state"] = "FINISHED"
                task["updated_at"] = utc_now()
                save_task(config, task)
                return task
            metadata = response.get("metadata") if isinstance(response.get("metadata"), dict) else {}
            url = metadata.get("url")
            if not isinstance(url, str) or not url:
                _save_error(task, "protocol_error", "completed response did not contain metadata.url")
                task["tracking_state"] = "ERROR"
                save_task(config, task)
                return task
            task["result"]["url"] = url
            task["result"]["download_state"] = "DOWNLOADING"
            save_task(config, task)
            try:
                local_path = _download_result(config, task, url)
            except ApiError as exc:
                _save_error(task, "download_failed", str(exc))
                task["result"]["download_state"] = "FAILED"
                task["tracking_state"] = "PAUSED"
                save_task(config, task)
                return task
            except (ClientError, OSError) as exc:
                _save_error(task, "download_failed", str(exc))
                task["result"]["download_state"] = "FAILED"
                task["tracking_state"] = "PAUSED"
                save_task(config, task)
                return task
            task["result"]["local_path"] = local_path
            task["result"]["download_state"] = "SUCCEEDED"
            task["result"]["url_redacted"] = urllib.parse.urlunparse(urllib.parse.urlparse(url)._replace(query="", fragment=""))
            task["result"]["url"] = None
            task["tracking_state"] = "FINISHED"
            task["updated_at"] = utc_now()
            save_task(config, task)
            return task
        else:
            _save_error(task, "protocol_error", f"unknown remote status: {status or '<empty>'}")
            task["tracking_state"] = "ERROR"
            save_task(config, task)
            return task
        save_task(config, task)
        if time.monotonic() - started >= config.task_timeout:
            task["tracking_state"] = "PAUSED"
            _save_error(task, "local_timeout", "local polling timeout reached")
            save_task(config, task)
            return task
        time.sleep(config.poll_interval)
    except (PollInterrupted, KeyboardInterrupt):
        task["tracking_state"] = "PAUSED"
        _save_error(task, "interrupted", "polling interrupted; resume with the same Task ID")
        save_task(config, task)
        return task
    finally:
        if can_install_handlers:
            for signum, handler in previous_handlers.items():
                signal.signal(signum, handler)


def summary(task: dict[str, Any]) -> dict[str, Any]:
    result_url = task.get("result", {}).get("url") or task.get("result", {}).get("url_redacted")
    if result_url:
        parsed = urllib.parse.urlparse(result_url)
        result_url = urllib.parse.urlunparse(parsed._replace(query="", fragment=""))
    return {
        "task_id": task["task_id"],
        "status": task.get("status"),
        "progress": task.get("progress"),
        "tracking_state": task.get("tracking_state"),
        "local_path": task.get("result", {}).get("local_path"),
        "result_url": result_url,
        "task_record": task.get("record_path"),
        "metadata": task.get("result", {}).get("metadata"),
        "last_error": task.get("last_error"),
    }


def generate(config: Config, args: argparse.Namespace) -> int:
    config.ensure_dirs()
    media = collect_media(args)
    output, explicit_file = _prepare_output(config, args.output)
    request_id = str(uuid.uuid4())
    submission = {
        "schema_version": 1,
        "client_request_id": request_id,
        "submission_state": "PREPARED",
        "created_at": utc_now(),
        "updated_at": utc_now(),
        "request": {
            "ratio": args.ratio,
            "duration": args.duration,
            "generate_audio": args.generate_audio,
            "watermark": args.watermark,
            "media_counts": {kind: sum(item.kind == kind for item in media) for kind in EXTENSIONS},
        },
        "output": {"requested": str(output), "explicit_file": explicit_file},
        "cleanup": {"objects": [], "updated_at": utc_now()},
        "last_error": None,
    }
    save_submission(config, submission)
    task: dict[str, Any] | None = None
    with submission_lock(config, request_id):
        def record_upload(item: dict[str, Any]) -> None:
            objects = submission["cleanup"]["objects"]
            existing = next(
                (
                    value
                    for value in objects
                    if isinstance(value, dict) and value.get("ownership_proof") == item.get("ownership_proof")
                ),
                None,
            )
            if existing is None:
                objects.append(item)
            elif existing is not item:
                existing.clear()
                existing.update(item)
            submission["updated_at"] = utc_now()
            save_submission(config, submission)

        try:
            if any(item.url is None for item in media):
                lifecycle_days = verify_bucket_lifecycle(config)
                submission["lifecycle"] = {"verified_at": utc_now(), "expiration_days": lifecycle_days}
                submission["updated_at"] = utc_now()
                save_submission(config, submission)
            uploaded = [upload_media(config, item, record_upload=record_upload) for item in media]
            payload = build_payload(
                args.prompt,
                uploaded,
                duration=args.duration,
                ratio=args.ratio,
                generate_audio=args.generate_audio,
                watermark=args.watermark,
            )
        except (ClientError, ConfigError, OSError):
            submission["submission_state"] = "REJECTED"
            submission["last_error"] = {"code": "pre_submission_failed", "message": "request failed before video submission"}
            cleanup_record_uploads(config, submission, force=True)
            save_submission(config, submission)
            raise
        submission["submission_state"] = "PENDING"
        submission["updated_at"] = utc_now()
        save_submission(config, submission)
        try:
            response = api_request(config, "POST", "/videos", payload=payload, request_id=request_id)
            task_id = _task_id_from_create(response)
        except ApiError as exc:
            code = _api_error_code(exc.body)
            should_query = exc.status in (0, 408, 429) or 500 <= exc.status < 600 or (
                exc.status == 409 and code == "submission_in_progress"
            )
            if should_query:
                submission, task = recover_submission_record(config, submission, retry_limit=0)
                if task is None:
                    state = submission["submission_state"]
                    raise ClientError(f"submission {state}; recover with client request id {request_id}") from exc
            else:
                submission["submission_state"] = "UNKNOWN" if code == "submission_unknown" else "REJECTED"
                submission["last_error"] = {
                    "code": code or (f"http_{exc.status}" if exc.status else "network_error"),
                    "message": _error_summary(exc.body),
                }
                submission["updated_at"] = utc_now()
                if submission["submission_state"] == "REJECTED":
                    cleanup_record_uploads(config, submission, force=True)
                save_submission(config, submission)
                raise ClientError(f"submission {submission['submission_state']}; client request id {request_id}") from exc
        except (ClientError, OSError) as exc:
            submission, task = recover_submission_record(config, submission, retry_limit=0)
            if task is None:
                raise ClientError(
                    f"submission {submission['submission_state']}; recover with client request id {request_id}"
                ) from exc
        if task is None:
            task = _new_task(task_id, request_id, payload, output, explicit_file, response if isinstance(response, dict) else {})
            task["cleanup"] = submission["cleanup"]
            record_path = save_task(config, task)
            task["record_path"] = str(record_path)
            save_task(config, task)
            submission["submission_state"] = "CONFIRMED"
            submission["task_id"] = task_id
            submission["last_error"] = None
            submission["cleanup"] = {"objects": [], "transferred_to_task_id": task_id, "updated_at": utc_now()}
            submission["updated_at"] = utc_now()
            save_submission(config, submission)
    if task is None:
        raise ClientError(f"submission UNKNOWN; recover with client request id {request_id}")
    with task_lock(config, task["task_id"]):
        task = poll_task(config, task)
    print(json.dumps(summary(task), ensure_ascii=False, indent=2))
    return 0 if task.get("tracking_state") == "FINISHED" and task.get("result", {}).get("download_state") == "SUCCEEDED" else 1


def submission_summary(submission: dict[str, Any]) -> dict[str, Any]:
    return {
        "client_request_id": submission["client_request_id"],
        "submission_state": submission.get("submission_state"),
        "task_id": submission.get("task_id"),
        "billing_state": submission.get("remote", {}).get("billing_state") if isinstance(submission.get("remote"), dict) else None,
        "created_at": submission.get("created_at"),
        "updated_at": submission.get("updated_at"),
        "last_error": submission.get("last_error"),
    }


def recover(config: Config, client_request_id: str) -> int:
    try:
        request_id = str(uuid.UUID(client_request_id))
    except ValueError as exc:
        raise ClientError("client request id must be a UUID") from exc
    if request_id != client_request_id:
        raise ClientError("client request id must be a canonical UUID")
    try:
        submission = load_submission(config, request_id)
    except ValueError:
        output, explicit_file = _prepare_output(config, None)
        submission = {
            "schema_version": 1,
            "client_request_id": request_id,
            "submission_state": "UNKNOWN",
            "created_at": utc_now(),
            "updated_at": utc_now(),
            "request": {},
            "output": {"requested": str(output), "explicit_file": explicit_file},
            "cleanup": {"objects": [], "updated_at": utc_now()},
            "last_error": None,
        }
        save_submission(config, submission)
    try:
        with submission_lock(config, request_id):
            submission, task = recover_submission_record(config, submission)
    except BusyTaskError:
        raise ClientError("submission is already being recovered by another process")
    if task is None:
        print(json.dumps(submission_summary(submission), ensure_ascii=False, indent=2))
        return 1
    with task_lock(config, task["task_id"]):
        task["record_path"] = str(task_path(config, task["task_id"]))
        task = poll_task(config, task)
    print(json.dumps(summary(task), ensure_ascii=False, indent=2))
    return 0 if task.get("tracking_state") == "FINISHED" and task.get("status") == "completed" and task.get("result", {}).get("download_state") == "SUCCEEDED" else 1


def resume(config: Config, task_id: str) -> int:
    task = load_task(config, task_id)
    task["record_path"] = str(task_path(config, task_id))
    try:
        with task_lock(config, task_id):
            task = poll_task(config, task)
    except BusyTaskError:
        raise ClientError("task is already being tracked by another process")
    print(json.dumps(summary(task), ensure_ascii=False, indent=2))
    return 0 if task.get("tracking_state") == "FINISHED" and task.get("status") == "completed" and task.get("result", {}).get("download_state") == "SUCCEEDED" else 1


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    generate_parser = sub.add_parser("generate")
    generate_parser.add_argument("--prompt", required=True)
    generate_parser.add_argument("--output")
    generate_parser.add_argument("--duration", type=int, default=10)
    generate_parser.add_argument("--ratio", default="16:9", choices=sorted(ALLOWED_RATIOS))
    generate_parser.add_argument("--image", action="append", default=[])
    generate_parser.add_argument("--image-url", action="append", default=[])
    generate_parser.add_argument("--audio", action="append", default=[])
    generate_parser.add_argument("--audio-url", action="append", default=[])
    generate_parser.add_argument("--video", action="append", default=[])
    generate_parser.add_argument("--video-url", action="append", default=[])
    generate_parser.add_argument("--generate-audio", dest="generate_audio", action="store_true", default=True)
    generate_parser.add_argument("--no-generate-audio", dest="generate_audio", action="store_false")
    generate_parser.add_argument("--watermark", action="store_true", default=False)
    sub.add_parser("list")
    resume_parser = sub.add_parser("resume")
    resume_parser.add_argument("task_id")
    recover_parser = sub.add_parser("recover")
    recover_parser.add_argument("client_request_id")
    return parser


def main() -> int:
    parser = make_parser()
    args = parser.parse_args()
    try:
        config = load_config()
        config.ensure_dirs()
        cleanup_expired_uploads(config)
        if args.command == "list":
            for task in list_tasks(config):
                task["record_path"] = str(task_path(config, task["task_id"]))
                print(json.dumps(summary(task), ensure_ascii=False))
            for submission in list_submissions(config):
                print(json.dumps(submission_summary(submission), ensure_ascii=False))
            return 0
        if args.command == "resume":
            return resume(config, args.task_id)
        if args.command == "recover":
            return recover(config, args.client_request_id)
        return generate(config, args)
    except (BusyTaskError, ConfigError, ClientError, OSError) as exc:
        print(f"VerdantFlare Video failed: {redact_message(str(exc))}", file=os.sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
