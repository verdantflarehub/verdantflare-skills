"""SQLite task, lease, turn, and event store for verdantflare-agent-grok."""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import time
import uuid

from .errors import BridgeError

ACTIVE = ("queued", "starting", "running", "waiting_input", "waiting_approval", "cancelling", "interrupted_unknown")
TERMINAL = ("completed", "failed", "cancelled", "timed_out")


def canonical(path: str | Path) -> str:
    candidate = Path(path)
    if not candidate.is_absolute():
        raise BridgeError("WORKSPACE_DENIED", "cwd must be an existing absolute directory")
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as error:
        raise BridgeError("WORKSPACE_DENIED", f"cwd cannot be resolved: {error}") from None
    if not resolved.is_dir():
        raise BridgeError("WORKSPACE_DENIED", "cwd must be a directory")
    return os.path.normcase(str(resolved))


def overlapping(first: str, second: str) -> bool:
    left, right = Path(first), Path(second)
    return left == right or left.is_relative_to(right) or right.is_relative_to(left)


def allowed_cwd(path: str | Path, roots: list[str | Path] | None) -> str:
    target = canonical(path)
    if not roots or "*" in [str(r).strip() for r in roots]:
        return target
    canonical_roots = [canonical(r) for r in roots]
    if not any(Path(target).is_relative_to(Path(r)) for r in canonical_roots):
        raise BridgeError(
            "WORKSPACE_DENIED",
            f"cwd '{target}' is outside configured allowed_roots {canonical_roots}",
        )
    return target


def fingerprint(operation: str, payload: dict) -> str:
    body = json.dumps({"op": operation, "payload": payload}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _owner_pid_alive(owner: str | None) -> bool:
    if not owner or ":" not in owner:
        return True
    pid_str = owner.split(":", 1)[0]
    if not pid_str.isdigit():
        return True
    pid = int(pid_str)
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        api = ctypes.WinDLL('kernel32', use_last_error=True)
        api.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        api.OpenProcess.restype = wintypes.HANDLE
        api.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        api.GetExitCodeProcess.restype = wintypes.BOOL
        api.CloseHandle.argtypes = [wintypes.HANDLE]
        api.CloseHandle.restype = wintypes.BOOL
        handle = api.OpenProcess(0x1000, False, pid)
        if not handle:
            # Only ERROR_INVALID_PARAMETER proves this PID does not exist.
            # Access denial is not evidence of death; heartbeat still applies.
            return ctypes.get_last_error() != 87
        try:
            code = wintypes.DWORD()
            if api.GetExitCodeProcess(handle, ctypes.byref(code)):
                return code.value == 259
            return True
        finally:
            api.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


class Store:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        try:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA busy_timeout=30000")
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS tasks(
                    id TEXT PRIMARY KEY,
                    state TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    cwd TEXT NOT NULL,
                    thread_id TEXT,
                    turn_id TEXT,
                    owner TEXT,
                    deadline REAL NOT NULL,
                    created REAL NOT NULL,
                    updated REAL NOT NULL,
                    result TEXT,
                    error TEXT
                );
                CREATE TABLE IF NOT EXISTS turns(
                    task_id TEXT NOT NULL,
                    turn_id TEXT NOT NULL,
                    state TEXT NOT NULL,
                    result TEXT,
                    error TEXT,
                    updated REAL NOT NULL,
                    PRIMARY KEY(task_id, turn_id)
                );
                CREATE TABLE IF NOT EXISTS requests(
                    id TEXT PRIMARY KEY,
                    digest TEXT NOT NULL,
                    task_id TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS commands(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL,
                    operation TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    state TEXT NOT NULL,
                    owner TEXT,
                    response TEXT,
                    created REAL NOT NULL,
                    updated REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS events(
                    seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS pending(
                    id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    rpc_id TEXT NOT NULL,
                    turn_id TEXT,
                    kind TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    status TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS owners(
                    id TEXT PRIMARY KEY,
                    heartbeat REAL NOT NULL
                );
                """
            )
        finally:
            db.close()

    @contextmanager
    def transaction(self):
        db = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA busy_timeout=30000")
        db.execute("BEGIN IMMEDIATE")
        try:
            yield db
            db.execute("COMMIT")
        except Exception:
            db.execute("ROLLBACK")
            raise
        finally:
            db.close()

    def heartbeat(self, owner: str) -> None:
        with self.transaction() as db:
            db.execute("INSERT OR REPLACE INTO owners VALUES(?,?)", (owner, time.time()))

    def mark_lost_owners(self, stale_before: float) -> int:
        with self.transaction() as db:
            rows = db.execute(
                """
                SELECT t.id, t.owner, o.heartbeat AS owner_hb
                FROM tasks t LEFT JOIN owners o ON t.owner = o.id
                WHERE t.state IN ('queued','starting','running','waiting_input','waiting_approval','cancelling')
                  AND t.owner IS NOT NULL
                """
            ).fetchall()
            lost_count = 0
            for row in rows:
                hb = row["owner_hb"]
                if hb is None or hb < stale_before or not _owner_pid_alive(row["owner"]):
                    lost_count += 1
                    if db.execute("SELECT 1 FROM commands WHERE task_id=? AND state='queued' AND operation IN ('start','continue')", (row['id'],)).fetchone() and self._task(db,row['id'])['state']=='queued':
                        db.execute("UPDATE tasks SET owner=NULL WHERE id=?", (row['id'],))
                        self._event(db,row['id'],'owner_lost_unsubmitted',{'retry_safe':True})
                        continue
                    db.execute("UPDATE commands SET state='unknown' WHERE task_id=? AND state IN ('queued','claimed')", (row['id'],))
                    self._state(db, row["id"], "interrupted_unknown", error={"code": "BACKEND_DISCONNECTED"})
            return lost_count

    def replay(self, operation: str, payload: dict) -> dict | None:
        digest = fingerprint(operation, payload)
        with self.transaction() as db:
            prior = db.execute("SELECT digest, task_id FROM requests WHERE id=?", (payload["request_id"],)).fetchone()
            if not prior:
                return None
            if prior["digest"] != digest:
                raise BridgeError("REQUEST_CONFLICT", "request_id was already used with different arguments")
            return self._task(db, prior["task_id"])

    def start(self, payload: dict, *, owner=None) -> dict:
        digest = fingerprint("start", payload)
        now = time.time()
        with self.transaction() as db:
            prior = db.execute("SELECT digest, task_id FROM requests WHERE id=?", (payload["request_id"],)).fetchone()
            if prior:
                if prior["digest"] != digest:
                    raise BridgeError("REQUEST_CONFLICT", "request_id was already used with different arguments")
                return self._task(db, prior["task_id"])
            for row in db.execute(f"SELECT cwd, thread_id FROM tasks WHERE state IN {ACTIVE}"):
                if overlapping(row["cwd"], payload["cwd"]) or (
                    payload.get("thread_id") and row["thread_id"] == payload["thread_id"]
                ):
                    raise BridgeError(
                        "SESSION_BUSY",
                        "An active or unknown task already holds this workspace or session lease",
                        retryable=True,
                    )
            task_id = str(uuid.uuid4())
            db.execute(
                "INSERT INTO tasks VALUES(?,?,?,?,?,?,?,?,?,?,NULL,NULL)",
                (
                    task_id,
                    "queued",
                    payload["mode"],
                    payload["cwd"],
                    payload.get("thread_id"),
                    None,
                    owner,
                    now + payload["timeout_seconds"],
                    now,
                    now,
                ),
            )
            db.execute("INSERT INTO requests VALUES(?,?,?)", (payload["request_id"], digest, task_id))
            self._queue(db, task_id, "start", payload)
            self._event(db, task_id, "queued", {"mode": payload["mode"], "session_id": payload.get("thread_id")})
            return self._task(db, task_id)

    def enqueue(self, operation: str, payload: dict, *, owner=None) -> dict:
        with self.transaction() as db:
            task = self._task(db, payload["task_id"])
            if "request_id" in payload:
                digest = fingerprint(operation, payload)
                prior = db.execute("SELECT digest, task_id FROM requests WHERE id=?", (payload["request_id"],)).fetchone()
                if prior:
                    if prior["digest"] != digest:
                        raise BridgeError("REQUEST_CONFLICT", "request_id was already used with different arguments")
                    return self._task(db, prior["task_id"])
            if task["state"] == "interrupted_unknown":
                raise BridgeError("SESSION_BUSY", "Task outcome is unknown and requires reconciliation before mutation", task_id=task["id"])
            if operation != "continue" and task["turn_id"] != payload.get("expected_turn_id"):
                raise BridgeError("STALE_TURN", "expected_turn_id does not match the active turn", task_id=task["id"])
            if operation == "continue":
                if task["state"] not in TERMINAL or task["turn_id"] != payload["expected_turn_id"]:
                    raise BridgeError("STALE_TURN", "Continue requires the last completed turn_id on a terminal task", task_id=task["id"])
                for row in db.execute(f"SELECT id, cwd, thread_id FROM tasks WHERE state IN {ACTIVE}"):
                    if row["id"] != task["id"] and (
                        overlapping(row["cwd"], task["cwd"]) or row["thread_id"] == task["thread_id"]
                    ):
                        raise BridgeError("SESSION_BUSY", "Workspace or session lease is currently busy", retryable=True, task_id=task["id"])
                now = time.time()
                db.execute(
                    "UPDATE tasks SET state='queued', owner=?, deadline=?, updated=?, result=NULL, error=NULL WHERE id=?",
                    (owner, now + payload["timeout_seconds"], now, task["id"]),
                )
            elif operation == "cancel":
                if task["state"] in TERMINAL:
                    return task
                self._state(db, task["id"], "cancelling")
            if "request_id" in payload:
                db.execute(
                    "INSERT INTO requests VALUES(?,?,?)",
                    (payload["request_id"], fingerprint(operation, payload), task["id"]),
                )
            self._queue(db, task["id"], operation, payload)
            self._event(db, task["id"], f"{operation}_queued", {})
            return self._task(db, task["id"])

    def claim(self, owner: str) -> dict | None:
        with self.transaction() as db:
            row = db.execute(
                """
                SELECT c.* FROM commands c
                JOIN tasks t ON t.id = c.task_id
                WHERE c.state='queued' AND (
                    (c.operation IN ('start','continue') AND t.owner IS NULL)
                    OR t.owner = ?
                )
                ORDER BY c.id LIMIT 1
                """,
                (owner,),
            ).fetchone()
            if not row:
                return None
            now = time.time()
            db.execute("UPDATE commands SET state='claimed', owner=?, updated=? WHERE id=?", (owner, now, row["id"]))
            if row["operation"] in ("start", "continue"):
                db.execute("UPDATE tasks SET owner=?, updated=? WHERE id=?", (owner, now, row["task_id"]))
            return dict(row) | {"payload": json.loads(row["payload"])}

    def command_done(self, command_id: int, owner: str, response: dict | None = None) -> None:
        with self.transaction() as db:
            db.execute(
                "UPDATE commands SET state='done', response=?, updated=? WHERE id=? AND owner=?",
                (json.dumps(response or {}), time.time(), command_id, owner),
            )

    def transition(
        self,
        task_id: str,
        owner: str,
        state: str,
        *,
        thread_id: str | None = None,
        turn_id: str | None = None,
        result: dict | None = None,
        error: dict | None = None,
    ) -> dict:
        with self.transaction() as db:
            task = self._owned_task(db, task_id, owner)
            if task["state"] == "cancelling" and state not in ("cancelled", "completed", "failed", "timed_out", "interrupted_unknown"):
                return task
            return self._state(db, task_id, state, thread_id=thread_id, turn_id=turn_id, result=result, error=error)

    def append_event(self, task_id: str, kind: str, payload: dict) -> None:
        with self.transaction() as db:
            self._event(db, task_id, kind, payload)

    def pending_list(self, task_id: str) -> list[dict]:
        with self.transaction() as db:
            rows = db.execute("SELECT * FROM pending WHERE task_id=? AND status='open'", (task_id,)).fetchall()
            return [dict(r) | {"payload": json.loads(r["payload"])} for r in rows]

    def task(self, task_id: str) -> dict:
        with self.transaction() as db:
            return self._task(db, task_id)

    def events(self, task_id: str, after: int = 0, limit: int = 50) -> dict:
        with self.transaction() as db:
            self._task(db, task_id)
            rows = db.execute(
                "SELECT * FROM events WHERE task_id=? AND seq>? ORDER BY seq LIMIT ?",
                (task_id, after, limit + 1),
            ).fetchall()
            sliced = rows[:limit]
            items = [
                {"seq": r["seq"], "kind": r["kind"], "created": r["created"], "payload": json.loads(r["payload"])}
                for r in sliced
            ]
            return {
                "events": items,
                "next_event": items[-1]["seq"] if items else after,
                "has_more": len(rows) > limit,
            }

    def result(self, task_id: str, turn_id: str | None = None) -> dict:
        with self.transaction() as db:
            task = self._task(db, task_id)
            state, selected_turn, raw_result, raw_error = task["state"], task["turn_id"], task["result"], task["error"]
            if turn_id and turn_id != task["turn_id"]:
                turn = db.execute("SELECT * FROM turns WHERE task_id=? AND turn_id=?", (task_id, turn_id)).fetchone()
                if not turn:
                    raise BridgeError("TASK_NOT_FOUND", f"Unknown turn_id {turn_id}", task_id=task_id)
                state, selected_turn, raw_result, raw_error = (
                    turn["state"],
                    turn["turn_id"],
                    turn["result"],
                    turn["error"],
                )
            ready = state in TERMINAL
            body = json.loads(raw_result) if raw_result else {}
            if raw_error:
                body.setdefault("unresolved", []).append(json.loads(raw_error))
            return {
                "task_id": task_id,
                "ready": ready,
                "state": state,
                "session_id": task["thread_id"],
                "thread_id": task["thread_id"],
                "turn_id": selected_turn,
                "result": body,
            }

    def _owned_task(self, db: sqlite3.Connection, task_id: str, owner: str) -> dict:
        task = self._task(db, task_id)
        if task["owner"] != owner:
            raise BridgeError("BACKEND_DISCONNECTED", "Task lease is no longer owned by this worker", task_id=task_id)
        return task

    def _task(self, db: sqlite3.Connection, task_id: str) -> dict:
        row = db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if not row:
            raise BridgeError("TASK_NOT_FOUND", f"Unknown task_id {task_id}", task_id=task_id)
        return dict(row)

    def _state(
        self,
        db: sqlite3.Connection,
        task_id: str,
        state: str,
        *,
        thread_id: str | None = None,
        turn_id: str | None = None,
        result: dict | None = None,
        error: dict | None = None,
    ) -> dict:
        current = self._task(db, task_id)
        now = time.time()
        owner = None if state in TERMINAL or state == "interrupted_unknown" else current["owner"]
        active_turn = turn_id or current["turn_id"]
        result_json = json.dumps(result, ensure_ascii=False) if result is not None else current["result"]
        error_json = json.dumps(error, ensure_ascii=False) if error is not None else current["error"]
        db.execute(
            "UPDATE tasks SET state=?, thread_id=?, turn_id=?, owner=?, updated=?, result=?, error=? WHERE id=?",
            (
                state,
                thread_id or current["thread_id"],
                active_turn,
                owner,
                now,
                result_json,
                error_json,
                task_id,
            ),
        )
        if active_turn:
            db.execute(
                "INSERT OR REPLACE INTO turns VALUES(?,?,?,?,?,?)",
                (task_id, active_turn, state, result_json, error_json, now),
            )
        if state in TERMINAL:
            db.execute("UPDATE pending SET status='closed' WHERE task_id=? AND status='open'", (task_id,))
        self._event(db, task_id, "state", {"state": state, "turn_id": active_turn, "error": error})
        return self._task(db, task_id)

    def _queue(self, db: sqlite3.Connection, task_id: str, operation: str, payload: dict) -> None:
        now = time.time()
        db.execute(
            "INSERT INTO commands(task_id,operation,payload,state,created,updated) VALUES(?,?,?,?,?,?)",
            (task_id, operation, json.dumps(payload, ensure_ascii=False), "queued", now, now),
        )

    def _event(self, db: sqlite3.Connection, task_id: str, kind: str, payload: dict) -> None:
        db.execute(
            "INSERT INTO events(task_id,kind,payload,created) VALUES(?,?,?,?)",
            (task_id, kind, json.dumps(payload, ensure_ascii=False), time.time()),
        )
