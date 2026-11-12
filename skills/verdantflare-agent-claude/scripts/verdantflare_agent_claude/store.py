"""Durable tasks, idempotent commands, and cross-process workspace leases for Claude MCP."""
from contextlib import closing, contextmanager
from pathlib import Path
import hashlib
import json
import os
import sqlite3
import time
import uuid

from .errors import BridgeError

TERMINAL = frozenset({"completed", "failed", "cancelled", "timed_out"})
STATES = TERMINAL | {
    "queued",
    "starting",
    "running",
    "waiting_input",
    "waiting_approval",
    "cancelling",
    "recovering",
    "interrupted_unknown",
}


def canonical(path):
    return os.path.normcase(str(Path(path).resolve(strict=True)))


def contains(parent, child):
    try:
        return os.path.commonpath([parent, child]) == parent
    except ValueError:
        return False


def allowed_cwd(path, roots):
    if not Path(path).is_absolute():
        raise BridgeError("WORKSPACE_DENIED", f"An absolute workspace path is required: {path}")
    try:
        real = canonical(path)
        if not Path(real).is_dir():
            raise ValueError("unavailable")
    except (ValueError, OSError):
        raise BridgeError("WORKSPACE_DENIED", f"Workspace path is unavailable or not a directory: {path}") from None
    unrestricted = not roots or "*" in roots
    if not unrestricted:
        canonical_roots = []
        for r in roots:
            try:
                canonical_roots.append(canonical(r))
            except OSError:
                canonical_roots.append(os.path.normcase(str(Path(r))))
        if not any(contains(cr, real) for cr in canonical_roots):
            roots_text = ", ".join(canonical_roots)
            raise BridgeError(
                "WORKSPACE_DENIED",
                f"Workspace {real} is outside configured allowed_roots [{roots_text}]",
            )
    return real


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


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
    def __init__(self, path):
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with closing(self.connect()) as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript('''
                CREATE TABLE IF NOT EXISTS tasks(
                    id TEXT PRIMARY KEY, mode TEXT NOT NULL, cwd TEXT NOT NULL,
                    thread_id TEXT, turn_id TEXT, state TEXT NOT NULL,
                    created REAL NOT NULL, deadline REAL NOT NULL, timeout INTEGER NOT NULL,
                    owner TEXT, updated REAL NOT NULL, error TEXT);
                CREATE TABLE IF NOT EXISTS requests(
                    id TEXT PRIMARY KEY, digest TEXT NOT NULL, task_id TEXT NOT NULL,
                    command_id TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS commands(
                    id TEXT PRIMARY KEY, task_id TEXT NOT NULL, operation TEXT NOT NULL,
                    payload TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'queued',
                    owner TEXT, created REAL NOT NULL, result TEXT);
                CREATE TABLE IF NOT EXISTS leases(
                    task_id TEXT PRIMARY KEY, cwd TEXT NOT NULL, thread_id TEXT);
                CREATE TABLE IF NOT EXISTS events(
                    seq INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL,
                    turn_id TEXT, kind TEXT NOT NULL, payload TEXT NOT NULL, created REAL NOT NULL);
                CREATE INDEX IF NOT EXISTS event_task ON events(task_id,seq);
                CREATE TABLE IF NOT EXISTS turns(
                    task_id TEXT NOT NULL, turn_id TEXT NOT NULL, state TEXT NOT NULL,
                    result TEXT, PRIMARY KEY(task_id,turn_id));
                CREATE TABLE IF NOT EXISTS owners(id TEXT PRIMARY KEY, heartbeat REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS pending(
                    id TEXT PRIMARY KEY, task_id TEXT NOT NULL, turn_id TEXT NOT NULL,
                    kind TEXT NOT NULL, payload TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'waiting');
            ''')

    def connect(self):
        db = sqlite3.connect(self.path, timeout=15, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=15000")
        return db

    @contextmanager
    def transaction(self):
        db = self.connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def _task(db, task_id):
        row = db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if row is None:
            raise BridgeError("TASK_NOT_FOUND", "Task does not exist", task_id=task_id)
        return dict(row)

    @staticmethod
    def _event(db, task, kind, data):
        now = time.time()
        db.execute("INSERT INTO events(task_id,turn_id,kind,payload,created) VALUES(?,?,?,?,?)",
                   (task["id"], task["turn_id"], kind, encode(data), now))
        db.execute("UPDATE tasks SET updated=? WHERE id=?", (now, task["id"]))

    @staticmethod
    def _lease(db, task):
        for row in db.execute("SELECT * FROM leases WHERE task_id<>?", (task["id"],)):
            if contains(row["cwd"], task["cwd"]) or contains(task["cwd"], row["cwd"]) or (
                    task["thread_id"] and task["thread_id"] == row["thread_id"]):
                raise BridgeError("SESSION_BUSY", "Workspace or session has an active lease", retryable=True)
        db.execute("INSERT OR REPLACE INTO leases VALUES(?,?,?)",
                   (task["id"], task["cwd"], task["thread_id"]))

    @staticmethod
    def _digest(operation, payload):
        return hashlib.sha256(encode({"operation": operation, "payload": payload}).encode()).hexdigest()

    def _replay(self, db, request_id, digest):
        row = db.execute("SELECT * FROM requests WHERE id=?", (request_id,)).fetchone()
        if row is None:
            return None
        if row["digest"] != digest:
            raise BridgeError("REQUEST_CONFLICT", "Request ID was used with different arguments")
        return self._task(db, row["task_id"])

    def _command(self, db, operation, payload, task_id, request_id, digest):
        command_id = str(uuid.uuid4())
        db.execute("INSERT INTO commands(id,task_id,operation,payload,created) VALUES(?,?,?,?,?)",
                   (command_id, task_id, operation, encode(payload), time.time()))
        db.execute("INSERT INTO requests VALUES(?,?,?,?)", (request_id, digest, task_id, command_id))

    def start(self, payload, *, owner=None):
        digest = self._digest("start", payload)
        with self.transaction() as db:
            replay = self._replay(db, payload["request_id"], digest)
            if replay is not None:
                return replay
            now, task_id = time.time(), str(uuid.uuid4())
            timeout = payload.get("timeout_seconds", 3600)
            sid = payload.get("session_id") or payload.get("thread_id")
            db.execute("INSERT INTO tasks VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                       (task_id, payload["mode"], payload["cwd"], sid, None,
                        "queued", now, now + timeout, timeout, owner, now, None))
            task = self._task(db, task_id)
            self._lease(db, task)
            self._command(db, "start", payload, task_id, payload["request_id"], digest)
            self._event(db, task, "queued", {})
            return task

    def enqueue(self, operation, payload, *, owner=None):
        digest = self._digest(operation, payload)
        with self.transaction() as db:
            if operation != "cancel":
                replay = self._replay(db, payload["request_id"], digest)
                if replay is not None:
                    return replay
            task = self._task(db, payload["task_id"])
            if task["turn_id"] != payload["expected_turn_id"]:
                raise BridgeError("STALE_TURN", "Expected turn does not match current turn")
            if operation == "continue":
                if task["state"] not in TERMINAL or db.execute(
                    "SELECT 1 FROM pending WHERE task_id=? AND state='waiting'", (task["id"],)
                ).fetchone():
                    raise BridgeError("SESSION_BUSY", "Previous turn is not finished")
                self._lease(db, task)
                db.execute("UPDATE tasks SET state='queued',owner=?,turn_id=NULL,error=NULL,deadline=?,updated=? WHERE id=?",
                           (owner, time.time() + task["timeout"], time.time(), task["id"]))
            elif operation == "cancel":
                if task["state"] in TERMINAL or task["state"] == "cancelling":
                    return task
                if task["state"] == "interrupted_unknown":
                    raise BridgeError("BACKEND_DISCONNECTED", "Unknown backend outcome requires terminal evidence; cancellation is not confirmed")
                if task["state"] in ("queued", "starting", "recovering"):
                    raise BridgeError("STALE_TURN", "Current execution has no confirmed cancellable turn")
                db.execute("UPDATE tasks SET state='cancelling',updated=? WHERE id=?", (time.time(), task["id"]))
            elif operation == "respond":
                pending = db.execute("SELECT * FROM pending WHERE id=? AND task_id=? AND turn_id=? AND state='waiting'",
                                     (payload["pending_request_id"], task["id"], task["turn_id"])).fetchone()
                if pending is None or task["deadline"] <= time.time():
                    raise BridgeError("STALE_TURN", "Input request is expired or no longer pending")
                if pending["kind"] == "approval":
                    raise BridgeError("APPROVAL_REQUIRES_UI", "Approval must be completed by a human in the host UI")
                db.execute("UPDATE pending SET state='responding' WHERE id=?", (pending["id"],))
            else:
                raise ValueError("Unknown command operation")
            request_id = payload.get("request_id") or ("cancel:" + str(uuid.uuid4()))
            self._command(db, operation, payload, task["id"], request_id, digest)
            task = self._task(db, task["id"])
            self._event(db, task, operation + "_queued", {})
            return task

    def heartbeat(self, owner):
        with self.transaction() as db:
            db.execute("INSERT OR REPLACE INTO owners VALUES(?,?)", (owner, time.time()))

    def assert_idle_for_naming(self, cwd, session_id):
        with closing(self.connect()) as db:
            for row in db.execute('SELECT * FROM leases'):
                if row['thread_id'] == session_id or contains(row['cwd'], cwd) or contains(cwd, row['cwd']):
                    raise BridgeError('SESSION_BUSY', 'Session or workspace has an active or unresolved task')

    def claim(self, owner):
        with self.transaction() as db:
            row = db.execute("""SELECT c.* FROM commands c JOIN tasks t ON c.task_id=t.id
                WHERE c.state='queued' AND (t.owner IS NULL OR t.owner=?)
                ORDER BY c.created,c.rowid LIMIT 1""", (owner,)).fetchone()
            if row is None:
                return None
            result = dict(row)
            db.execute("UPDATE commands SET state='claimed',owner=? WHERE id=?", (owner, row["id"]))
            db.execute("UPDATE tasks SET owner=?,updated=? WHERE id=?", (owner, time.time(), row["task_id"]))
            result["payload"] = json.loads(result["payload"])
            return result

    def command_done(self, command_id, owner, result):
        with self.transaction() as db:
            db.execute("UPDATE commands SET state='done',result=? WHERE id=? AND owner=? AND state='claimed'",
                       (encode(result), command_id, owner))

    def transition(self, task_id, owner, state, *, thread_id=None, turn_id=None, result=None, error=None):
        if state not in STATES:
            raise ValueError("Unknown state")
        with self.transaction() as db:
            task = self._task(db, task_id)
            if task["owner"] != owner:
                raise BridgeError("BACKEND_DISCONNECTED", "Worker no longer owns this task")
            if task["state"] in TERMINAL or task["state"] == "interrupted_unknown":
                raise BridgeError("STALE_TURN", "Terminal turn cannot change without a continuation")
            if task["state"] == "cancelling" and state in {"running", "starting", "waiting_input", "waiting_approval"}:
                state = "cancelling"
            thread_id, turn_id = thread_id or task["thread_id"], turn_id or task["turn_id"]
            db.execute("UPDATE tasks SET state=?,thread_id=?,turn_id=?,updated=?,error=? WHERE id=?",
                       (state, thread_id, turn_id, time.time(), encode(error) if error else None, task_id))
            if turn_id and state not in ("queued", "starting"):
                db.execute("INSERT INTO turns VALUES(?,?,?,?) ON CONFLICT(task_id,turn_id) DO UPDATE SET state=excluded.state,result=COALESCE(excluded.result,turns.result)",
                           (task_id, turn_id, state, encode(result) if result is not None else None))
            if state in TERMINAL:
                db.execute("DELETE FROM leases WHERE task_id=?", (task_id,))
                db.execute("UPDATE pending SET state='expired' WHERE task_id=? AND state IN ('waiting','responding')", (task_id,))
            else:
                db.execute("UPDATE leases SET thread_id=? WHERE task_id=?", (thread_id, task_id))
            if state == "interrupted_unknown":
                db.execute("UPDATE commands SET state='unknown' WHERE task_id=? AND state IN ('queued','claimed')", (task_id,))
            self._event(db, self._task(db, task_id), "state", {"state": state})

    def mark_lost_owners(self, stale_before):
        with self.transaction() as db:
            rows = db.execute("""SELECT t.*, o.heartbeat AS owner_hb FROM tasks t LEFT JOIN owners o ON t.owner=o.id
                WHERE t.owner IS NOT NULL
                AND t.state NOT IN ('completed','failed','cancelled','timed_out','interrupted_unknown')""").fetchall()
            lost_count = 0
            for row in rows:
                task = dict(row)
                hb = task.get("owner_hb")
                if hb is None or hb < stale_before or not _owner_pid_alive(task["owner"]):
                    lost_count += 1
                    if task['state'] == 'queued' and db.execute("SELECT 1 FROM commands WHERE task_id=? AND state='queued' AND operation IN ('start','continue')", (task['id'],)).fetchone():
                        # The transaction proves no worker claimed or submitted it.
                        db.execute('UPDATE tasks SET owner=NULL,updated=? WHERE id=?', (time.time(), task['id']))
                        self._event(db, task, 'owner_lost_unsubmitted', {'retry_safe': True})
                        continue
                    db.execute("UPDATE tasks SET state='interrupted_unknown',owner=NULL,updated=? WHERE id=?", (time.time(), task["id"]))
                    if task["turn_id"]:
                        db.execute("UPDATE turns SET state='interrupted_unknown' WHERE task_id=? AND turn_id=?", (task["id"], task["turn_id"]))
                    db.execute("UPDATE pending SET state='expired' WHERE task_id=? AND state IN ('waiting','responding')", (task["id"],))
                    db.execute("UPDATE commands SET state='unknown' WHERE task_id=? AND state IN ('queued','claimed')", (task["id"],))
                    self._event(db, task, "owner_lost", {"previous_state": task["state"]})
            db.execute(
                "DELETE FROM leases WHERE task_id IN (SELECT id FROM tasks WHERE state IN ('completed','failed','cancelled','timed_out'))"
            )
            return lost_count

    def task(self, task_id):
        db = self.connect()
        try:
            return self._task(db, task_id)
        finally:
            db.close()

    def events(self, task_id, after=0, limit=50):
        db = self.connect()
        try:
            self._task(db, task_id)
            rows = db.execute("SELECT * FROM events WHERE task_id=? AND seq>? ORDER BY seq LIMIT ?",
                              (task_id, after, limit + 1)).fetchall()
            items = [{**dict(r), "payload": json.loads(r["payload"])} for r in rows[:limit]]
            return {"events": items, "has_more": len(rows) > limit,
                    "next_event": items[-1]["seq"] if items else after}
        finally:
            db.close()

    def append_event(self, task_id, kind, payload):
        with self.transaction() as db:
            self._event(db, self._task(db, task_id), kind, payload)

    def pending_add(self, task_id, turn_id, kind, payload):
        request_id = str(uuid.uuid4())
        with self.transaction() as db:
            db.execute("INSERT INTO pending(id,task_id,turn_id,kind,payload) VALUES(?,?,?,?,?)",
                       (request_id, task_id, turn_id, kind, encode(payload)))
        return request_id

    def pending_list(self, task_id):
        with closing(self.connect()) as db:
            return [{**dict(row), "payload": json.loads(row["payload"])} for row in db.execute(
                "SELECT * FROM pending WHERE task_id=? AND state IN ('waiting','responding')", (task_id,))]

    def pending_done(self, request_id):
        with self.transaction() as db:
            db.execute("UPDATE pending SET state='done' WHERE id=?", (request_id,))

    def replay(self, operation, payload):
        with self.transaction() as db:
            return self._replay(db, payload["request_id"], self._digest(operation, payload))

    def result(self, task_id, turn_id=None):
        with closing(self.connect()) as db:
            task = self._task(db, task_id)
            explicit_turn = turn_id is not None
            turn_id = turn_id or task["turn_id"]
            turn = db.execute("SELECT * FROM turns WHERE task_id=? AND turn_id=?", (task_id, turn_id)).fetchone()
            if explicit_turn and turn is None:
                raise BridgeError("TASK_NOT_FOUND", "Turn does not exist for this task")
            result = json.loads(turn["result"]) if turn and turn["result"] else None
            if not turn and task["state"] in TERMINAL:
                result = {"answer": "", "commands": [], "file_changes": [],
                          "unresolved": [json.loads(task["error"])] if task["error"] else []}
            return {
                "task_id": task_id,
                "session_id": task["thread_id"],
                "thread_id": task["thread_id"],
                "turn_id": turn_id,
                "state": turn["state"] if turn else task["state"],
                "ready": (turn["state"] if turn else task["state"]) in TERMINAL,
                "result": result,
            }
