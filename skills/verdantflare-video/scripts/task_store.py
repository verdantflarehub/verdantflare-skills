#!/usr/bin/env python3
"""Atomic task and submission persistence for VerdantFlare Video."""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Any, Iterator

from config import Config

if os.name == "nt":
    import msvcrt
else:
    import fcntl


class BusyTaskError(RuntimeError):
    """Raised when another process is tracking the same task."""


def task_filename(task_id: str) -> str:
    return hashlib.sha256(task_id.encode("utf-8")).hexdigest() + ".json"


def task_path(config: Config, task_id: str) -> Path:
    return config.tasks_dir / task_filename(task_id)


def submission_path(config: Config, client_request_id: str) -> Path:
    return config.submissions_dir / f"{_canonical_request_id(client_request_id)}.json"


def _canonical_request_id(client_request_id: str) -> str:
    try:
        parsed = uuid.UUID(client_request_id)
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError("client_request_id must be a UUID") from exc
    canonical = str(parsed)
    if client_request_id != canonical:
        raise ValueError("client_request_id must be a canonical UUID")
    return canonical


def _fsync_directory(path: Path) -> None:
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def atomic_write(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    encoded = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", delete=False) as stream:
        temporary = Path(stream.name)
        stream.write(encoded)
        stream.flush()
        os.fsync(stream.fileno())
    try:
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
        os.chmod(path, 0o600)
        _fsync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def save_task(config: Config, task: dict[str, Any]) -> Path:
    task_id = str(task.get("task_id", ""))
    if not task_id:
        raise ValueError("task_id is required")
    path = task_path(config, task_id)
    atomic_write(path, task)
    return path


def load_task(config: Config, task_id: str) -> dict[str, Any]:
    path = task_path(config, task_id)
    try:
        task = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"task record is unreadable: {path}") from exc
    if task.get("task_id") != task_id or task.get("schema_version") != 1:
        raise ValueError(f"task record is invalid: {path}")
    return task


def save_submission(config: Config, submission: dict[str, Any]) -> Path:
    request_id = str(submission.get("client_request_id", ""))
    if not request_id:
        raise ValueError("client_request_id is required")
    path = submission_path(config, request_id)
    atomic_write(path, submission)
    return path


def load_submission(config: Config, client_request_id: str) -> dict[str, Any]:
    path = submission_path(config, client_request_id)
    try:
        submission = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("submission record is unreadable") from exc
    if submission.get("client_request_id") != client_request_id or submission.get("schema_version") != 1:
        raise ValueError("submission record is invalid")
    return submission


def delete_submission(config: Config, client_request_id: str) -> None:
    submission_path(config, client_request_id).unlink(missing_ok=True)


def list_submissions(config: Config) -> list[dict[str, Any]]:
    config.ensure_dirs()
    submissions: list[dict[str, Any]] = []
    for path in sorted(config.submissions_dir.glob("*.json")):
        try:
            submission = json.loads(path.read_text(encoding="utf-8"))
            request_id = str(submission.get("client_request_id", ""))
            if submission.get("schema_version") != 1 or path != submission_path(config, request_id):
                raise ValueError("invalid submission record")
            submissions.append(submission)
        except (OSError, ValueError, json.JSONDecodeError):
            continue
    return submissions


def list_tasks(config: Config) -> list[dict[str, Any]]:
    config.ensure_dirs()
    tasks: list[dict[str, Any]] = []
    for path in sorted(config.tasks_dir.glob("*.json")):
        try:
            task = json.loads(path.read_text(encoding="utf-8"))
            task_id = task.get("task_id")
            if task.get("schema_version") != 1 or not isinstance(task_id, str) or not task_id:
                raise ValueError("invalid schema")
            if path.name != task_filename(task_id):
                raise ValueError("task filename digest mismatch")
            tasks.append(task)
        except (OSError, ValueError, json.JSONDecodeError):
            quarantine = config.quarantine_dir / path.name
            shutil.move(str(path), str(quarantine))
    return tasks


@contextlib.contextmanager
def task_lock(config: Config, task_id: str, *, blocking: bool = False) -> Iterator[None]:
    config.ensure_dirs()
    path = config.locks_dir / (hashlib.sha256(task_id.encode("utf-8")).hexdigest() + ".lock")
    with path.open("a+") as stream:
        os.chmod(path, 0o600)
        if os.name == "nt":
            stream.seek(0, os.SEEK_END)
            if stream.tell() == 0:
                stream.write("0")
                stream.flush()
            stream.seek(0)
            try:
                mode = msvcrt.LK_LOCK if blocking else msvcrt.LK_NBLCK
                msvcrt.locking(stream.fileno(), mode, 1)
            except OSError as exc:
                raise BusyTaskError(task_id) from exc
        else:
            flags = fcntl.LOCK_EX
            if not blocking:
                flags |= fcntl.LOCK_NB
            try:
                fcntl.flock(stream.fileno(), flags)
            except BlockingIOError as exc:
                raise BusyTaskError(task_id) from exc
        try:
            yield
        finally:
            if os.name == "nt":
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


@contextlib.contextmanager
def submission_lock(config: Config, client_request_id: str, *, blocking: bool = False) -> Iterator[None]:
    request_id = _canonical_request_id(client_request_id)
    with task_lock(config, "submission:" + request_id, blocking=blocking):
        yield


def find_task(config: Config, task_id: str) -> dict[str, Any]:
    return load_task(config, task_id)
