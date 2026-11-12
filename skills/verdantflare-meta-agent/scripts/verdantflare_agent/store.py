"""Transactional identity registry and durable outbox. No network or subprocess calls."""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import sqlite3
import time
import uuid

from check_handoff import validate, endpoint, positive, text, relative_file


class Conflict(ValueError):
    """A stable public error code, never an untrusted payload or filesystem path."""


def encode(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(encode(value).encode("utf-8")).hexdigest()


class Store:
    def __init__(self, database, workspace, *, clock=time.time):
        self.clock = clock
        self.workspace = Path(workspace).resolve()
        if not self.workspace.is_dir():
            raise Conflict("WORKSPACE_MISSING")
        self.path = Path(database).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(self.path), timeout=30, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS sessions(key TEXT PRIMARY KEY, endpoint TEXT NOT NULL, name TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS plans(key TEXT PRIMARY KEY, name TEXT NOT NULL, path TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS tasks(plan_id TEXT NOT NULL, key TEXT NOT NULL,
                label TEXT NOT NULL, change_path TEXT NOT NULL, PRIMARY KEY(plan_id,key));
            CREATE TABLE IF NOT EXISTS assignments(
                id TEXT NOT NULL, revision INTEGER NOT NULL, handoff TEXT NOT NULL,
                PRIMARY KEY(id, revision));
            CREATE TABLE IF NOT EXISTS messages(
                seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE NOT NULL, body TEXT NOT NULL,
                hash TEXT NOT NULL, state TEXT NOT NULL, created REAL NOT NULL, expires REAL NOT NULL,
                due REAL NOT NULL, attempts INTEGER NOT NULL DEFAULT 0, max_attempts INTEGER NOT NULL,
                owner TEXT, token TEXT, lease_until REAL, route_generation INTEGER NOT NULL,
                receipt TEXT, ack TEXT, error TEXT, cursor TEXT, observed INTEGER NOT NULL DEFAULT 0);
            CREATE INDEX IF NOT EXISTS queue ON messages(state,due,seq);
            CREATE TABLE IF NOT EXISTS events(
                seq INTEGER PRIMARY KEY AUTOINCREMENT, time REAL NOT NULL, kind TEXT NOT NULL,
                subject TEXT NOT NULL, detail TEXT NOT NULL);
        """)
        try:
            with self.transaction():
                old = self.db.execute("SELECT value FROM meta WHERE key='workspace'").fetchone()
                if old and old[0] != str(self.workspace):
                    raise Conflict("WORKSPACE_MISMATCH")
                self.db.execute("INSERT OR IGNORE INTO meta VALUES('workspace',?)", (str(self.workspace),))
        except BaseException:
            self.db.close()
            raise

    def close(self):
        self.db.close()

    @contextmanager
    def transaction(self):
        self.db.execute("BEGIN IMMEDIATE")
        try:
            yield
        except BaseException:
            self.db.rollback()
            raise
        else:
            self.db.commit()

    def event(self, kind, subject, detail=None):
        self.db.execute("INSERT INTO events(time,kind,subject,detail) VALUES(?,?,?,?)",
                        (self.clock(), kind, subject, encode(detail or {})))

    def session(self, key):
        row = self.db.execute("SELECT * FROM sessions WHERE key=?", (key,)).fetchone()
        if not row:
            raise Conflict("SESSION_NOT_REGISTERED")
        return {**json.loads(row["endpoint"]), "display_name": row["name"]}

    def sessions(self):
        return [self.session(row[0]) for row in self.db.execute("SELECT key FROM sessions ORDER BY key")]

    def _unique_native(self, ep):
        for row in self.db.execute("SELECT key,endpoint FROM sessions WHERE key<>?", (ep["session_key"],)):
            other = json.loads(row["endpoint"])
            if (other["agent"], other["native_session_id"]) == (ep["agent"], ep["native_session_id"]):
                raise Conflict("SESSION_ALIAS_CONFLICT")

    def handoff(self, assignment_id, revision=None):
        if revision is None:
            row = self.db.execute("SELECT handoff FROM assignments WHERE id=? ORDER BY revision DESC LIMIT 1",
                                  (assignment_id,)).fetchone()
        else:
            row = self.db.execute("SELECT handoff FROM assignments WHERE id=? AND revision=?",
                                  (assignment_id, revision)).fetchone()
        if not row:
            raise Conflict("ASSIGNMENT_NOT_REGISTERED")
        result = json.loads(row[0])
        plan = self.db.execute("SELECT * FROM plans WHERE key=?", (result["plan_id"],)).fetchone()
        task = self.db.execute("SELECT * FROM tasks WHERE plan_id=? AND key=?", (result["plan_id"], result["task_key"])).fetchone()
        if plan:
            result.update(plan_name=plan["name"], plan_path=plan["path"])
        if task:
            result.update(task_label=task["label"], change_path=task["change_path"])
        return result

    def register(self, handoff):
        validate(handoff, self.workspace)
        with self.transaction():
            previous = self.db.execute("SELECT handoff FROM assignments WHERE id=? ORDER BY revision DESC LIMIT 1",
                                       (handoff["assignment_id"],)).fetchone()
            if previous:
                old = json.loads(previous[0])
                if old == handoff or self.handoff(handoff["assignment_id"]) == handoff:
                    return {"registered": True, "duplicate": True}
                if handoff["revision"] != old["revision"] + 1:
                    raise Conflict("REVISION_CONFLICT")
                if any(handoff[k] != old[k] for k in ("plan_id", "task_key")):
                    raise Conflict("ASSIGNMENT_IDENTITY_CONFLICT")
            elif handoff["revision"] != 1:
                raise Conflict("INITIAL_REVISION_REQUIRED")
            for role in ("sender", "recipient", "reply_to"):
                ep = handoff[role]
                prior = self.db.execute("SELECT endpoint FROM sessions WHERE key=?", (ep["session_key"],)).fetchone()
                if prior and json.loads(prior[0]) != ep:
                    raise Conflict("SESSION_ADDRESS_CONFLICT")
                self._unique_native(ep)
                label = "统筹" if role != "recipient" else handoff["task_label"]
                role_name = "统筹" if role != "recipient" else "执行"
                name = f'{handoff["plan_name"]}｜{label}｜{role_name}｜{ep["agent"]}'
                self.db.execute("INSERT OR IGNORE INTO sessions VALUES(?,?,?)", (ep["session_key"], encode(ep), name))
            self.db.execute("INSERT INTO plans VALUES(?,?,?) ON CONFLICT(key) DO UPDATE SET name=excluded.name,path=excluded.path",
                            (handoff["plan_id"], handoff["plan_name"], handoff["plan_path"]))
            task = self.db.execute("SELECT * FROM tasks WHERE plan_id=? AND key=?", (handoff["plan_id"], handoff["task_key"])).fetchone()
            if task and (task["label"] != handoff["task_label"] or task["change_path"] != handoff["change_path"]):
                raise Conflict("TASK_LOCATION_REQUIRES_RELINK")
            self.db.execute("INSERT OR IGNORE INTO tasks VALUES(?,?,?,?)",
                            (handoff["plan_id"], handoff["task_key"], handoff["task_label"], handoff["change_path"]))
            self.db.execute("INSERT INTO assignments VALUES(?,?,?)", (handoff["assignment_id"], handoff["revision"], encode(handoff)))
            self.event("registered", handoff["assignment_id"], {"revision": handoff["revision"]})
        return {"registered": True, "duplicate": False}

    def relink(self, plan_id, plan_name, plan_path, tasks):
        for label, value in (("plan_id", plan_id), ("plan_name", plan_name)):
            text(value, label)
        relative_file(self.workspace, plan_path, "plan_path", exists=True)
        if not isinstance(tasks, list):
            raise Conflict("INVALID_TASK_LINKS")
        keys = set()
        for task in tasks:
            if not isinstance(task, dict) or set(task) != {"task_key", "task_label", "change_path"}:
                raise Conflict("INVALID_TASK_LINKS")
            for key in ("task_key", "task_label"):
                text(task[key], key)
            relative_file(self.workspace, task["change_path"], "change_path", exists=True)
            if task["task_key"] in keys:
                raise Conflict("DUPLICATE_TASK_LINK")
            keys.add(task["task_key"])
        with self.transaction():
            if not self.db.execute("SELECT 1 FROM plans WHERE key=?", (plan_id,)).fetchone():
                raise Conflict("PLAN_NOT_REGISTERED")
            self.db.execute("UPDATE plans SET name=?,path=? WHERE key=?", (plan_name, plan_path, plan_id))
            for task in tasks:
                count = self.db.execute("UPDATE tasks SET label=?,change_path=? WHERE plan_id=? AND key=?",
                    (task["task_label"], task["change_path"], plan_id, task["task_key"])).rowcount
                if count != 1:
                    raise Conflict("TASK_NOT_REGISTERED")
            self.event("relinked", plan_id, {"plan_path": plan_path, "tasks": tasks})

    def rename(self, session_key, name):
        text(name, "name")
        with self.transaction():
            self.session(session_key)
            self.db.execute("UPDATE sessions SET name=? WHERE key=?", (name, session_key))
            self.event("logical_name_changed", session_key)
        return self.session(session_key)

    def takeover(self, new_endpoint, expected_generation, authorization):
        endpoint(new_endpoint, "endpoint")
        positive(expected_generation, "expected_generation")
        text(authorization, "authorization")
        with self.transaction():
            old = self.session(new_endpoint["session_key"])
            if old["generation"] != expected_generation or new_endpoint["generation"] != expected_generation + 1:
                raise Conflict("STALE_GENERATION")
            self._unique_native(new_endpoint)
            self.db.execute("UPDATE sessions SET endpoint=? WHERE key=?", (encode(new_endpoint), new_endpoint["session_key"]))
            self.event("takeover", new_endpoint["session_key"], {"from_generation": expected_generation,
                       "to_generation": new_endpoint["generation"], "authorization": authorization})

    def migrate(self, message_id, expected_generation):
        with self.transaction():
            row = self.get(message_id)
            current = self.session(row["body"]["recipient"])
            if row["state"] not in {"queued", "retry_wait"}:
                raise Conflict("MIGRATION_REQUIRES_UNSENT")
            if row["route_generation"] != expected_generation or current["generation"] <= expected_generation:
                raise Conflict("STALE_GENERATION")
            self.db.execute("UPDATE messages SET route_generation=?,due=? WHERE id=?",
                            (current["generation"], self.clock(), message_id))
            self.event("migrated", message_id, {"from_generation": expected_generation, "to_generation": current["generation"]})

    def enqueue(self, body, *, ttl=86400, max_attempts=20):
        with self.transaction():
            return self._enqueue(body, ttl=ttl, max_attempts=max_attempts)

    def _enqueue(self, body, *, ttl=86400, max_attempts=20):
        fields = {"message_id", "kind", "plan_id", "task_key", "assignment_id", "revision", "sender",
                  "sender_generation", "recipient", "recipient_generation", "causation_id", "payload"}
        if not isinstance(body, dict) or set(body) != fields:
            raise Conflict("INVALID_MESSAGE_FIELDS")
        for key in fields - {"payload", "revision", "sender_generation", "recipient_generation"}:
            text(body[key], key)
        for key in ("revision", "sender_generation", "recipient_generation"):
            positive(body[key], key)
        if not isinstance(body["payload"], dict) or len(encode(body).encode("utf-8")) > 262144:
            raise Conflict("INVALID_MESSAGE_PAYLOAD")
        positive(ttl, "ttl")
        positive(max_attempts, "max_attempts")
        value_hash = digest({"body": body, "ttl": ttl, "max_attempts": max_attempts})
        existing = self.db.execute("SELECT hash FROM messages WHERE id=?", (body["message_id"],)).fetchone()
        if existing:
            if existing[0] != value_hash:
                raise Conflict("MESSAGE_ID_CONFLICT")
            return self.get(body["message_id"])
        h = self.handoff(body["assignment_id"])
        if any(body[k] != h[k] for k in ("plan_id", "task_key", "revision")):
            raise Conflict("STALE_ASSIGNMENT")
        if body["kind"] in {"assignment", "review_request", "cancel"}:
            sender, recipient = h["sender"], h["recipient"]
        elif body["kind"] in {"result", "failed", "blocked", "review_result"}:
            sender, recipient = h["recipient"], h["reply_to"]
        else:
            raise Conflict("INVALID_MESSAGE_KIND")
        if body["sender"] != sender["session_key"] or body["recipient"] != recipient["session_key"]:
            raise Conflict("UNAUTHORIZED_MESSAGE_ROUTE")
        for key in ("sender", "recipient"):
            if self.session(body[key])["generation"] != body[key + "_generation"]:
                raise Conflict("STALE_GENERATION")
        if body["kind"] in {"result", "review_result"}:
            source = self.get(body["causation_id"])
            state = body["payload"].get("backend_state")
            receipt = body["payload"].get("source_receipt")
            if (state not in {"completed", "failed", "cancelled", "timed_out"}
                    or not receipt or receipt != source["receipt"]
                    or source["body"]["assignment_id"] != body["assignment_id"]
                    or source["body"]["revision"] != body["revision"]
                    or source["body"]["kind"] not in {"assignment", "review_request"}):
                raise Conflict("RESULT_SOURCE_MISMATCH")
            expected_id = "result-" + digest([body["assignment_id"], body["revision"], receipt["task_id"], receipt["turn_id"], state])
            if body["message_id"] != expected_id:
                raise Conflict("RESULT_ID_MUST_MATCH_SOURCE")
        now = self.clock()
        self.db.execute("""INSERT INTO messages(id,body,hash,state,created,expires,due,max_attempts,route_generation)
                        VALUES(?,?,?,'queued',?,?,?,?,?)""",
                        (body["message_id"], encode(body), value_hash, now, now + ttl, now, max_attempts, body["recipient_generation"]))
        self.event("queued", body["message_id"])
        return self.get(body["message_id"])

    def get(self, message_id):
        row = self.db.execute("SELECT * FROM messages WHERE id=?", (message_id,)).fetchone()
        if not row:
            raise Conflict("MESSAGE_NOT_FOUND")
        value = dict(row)
        for key in ("body", "receipt", "ack", "cursor"):
            value[key] = json.loads(value[key]) if value[key] is not None else None
        return value

    def pending(self, states):
        placeholders = ",".join("?" for _ in states)
        return [self.get(row[0]) for row in self.db.execute(
            f"SELECT id FROM messages WHERE state IN ({placeholders}) ORDER BY seq", tuple(states))]

    def is_current(self, row):
        b = row["body"]
        return (self.handoff(b["assignment_id"])["revision"] == b["revision"]
                and (b["kind"] in {"assignment", "review_request", "cancel"}
                     or self.session(b["sender"])["generation"] == b["sender_generation"])
                and self.session(b["recipient"])["generation"] == row["route_generation"])

    def recover(self):
        with self.transaction():
            rows = self.db.execute("SELECT id FROM messages WHERE state='delivering' AND lease_until<=?", (self.clock(),)).fetchall()
            for row in rows:
                self.db.execute("UPDATE messages SET state='unknown',error='WORKER_LEASE_EXPIRED' WHERE id=?", (row[0],))
                self.event("unknown", row[0], {"reason": "WORKER_LEASE_EXPIRED"})
        return len(rows)

    def claim(self, owner, *, lease_seconds=60):
        text(owner, "owner")
        positive(lease_seconds, "lease_seconds")
        with self.transaction():
            for row in self.pending({"queued", "retry_wait"}):
                b = row["body"]
                if (self.handoff(b["assignment_id"])["revision"] != b["revision"]
                        or self.session(b["sender"])["generation"] != b["sender_generation"]):
                    self.db.execute("UPDATE messages SET state='rejected',error='STALE_ROUTE_OR_REVISION' WHERE id=?", (row["id"],))
                    self.event("rejected", row["id"], {"reason": "STALE_ROUTE_OR_REVISION"})
                    continue
                if row["expires"] <= self.clock() or row["attempts"] >= row["max_attempts"]:
                    self.db.execute("UPDATE messages SET state='expired',error='DELIVERY_BUDGET_EXHAUSTED' WHERE id=?", (row["id"],))
                    self.event("expired", row["id"])
                    continue
                if self.session(b["recipient"])["generation"] != row["route_generation"]:
                    # Keep unsent rows migratable; takeover never silently reroutes them.
                    if row["error"] != "MIGRATION_REQUIRED":
                        self.db.execute("UPDATE messages SET error='MIGRATION_REQUIRED' WHERE id=?", (row["id"],))
                        self.event("migration_required", row["id"])
                    continue
                if row["due"] > self.clock():
                    continue
                self.db.execute("""UPDATE messages SET state='delivering',attempts=attempts+1,owner=?,token=?,lease_until=?
                                WHERE id=?""", (owner, uuid.uuid4().hex, self.clock() + lease_seconds, row["id"]))
                self.event("delivering", row["id"])
                return self.get(row["id"])
        return None

    def _claimed(self, message_id, token):
        row = self.get(message_id)
        if row["token"] != token or row["state"] not in {"delivering", "unknown"}:
            raise Conflict("CLAIM_LOST")
        return row

    def renew(self, message_id, token, *, lease_seconds=60):
        with self.transaction():
            row = self._claimed(message_id, token)
            if row["state"] != "delivering":
                return False
            self.db.execute("UPDATE messages SET lease_until=? WHERE id=?", (self.clock() + lease_seconds, message_id))
        return True

    def delivered(self, message_id, token, receipt):
        with self.transaction():
            row = self._claimed(message_id, token)
            if not isinstance(receipt, dict) or not all(isinstance(receipt.get(k), str) and receipt[k] for k in ("session_id", "task_id", "turn_id")):
                raise Conflict("INVALID_RECEIPT")
            # The endpoint snapshot used for the call is retained in the claim's generation.
            current = self.session(row["body"]["recipient"])
            if current["generation"] != row["route_generation"] or receipt["session_id"] != current["native_session_id"]:
                raise Conflict("RECEIPT_ROUTE_MISMATCH")
            self.db.execute("UPDATE messages SET state='delivered',receipt=?,error=NULL,lease_until=NULL WHERE id=?",
                            (encode(receipt), message_id))
            self.event("delivered", message_id)

    def delivery_error(self, message_id, token, *, known_unsent=False, reason="UNKNOWN_DELIVERY"):
        allowed = {"SESSION_BUSY", "OFFLINE", "UNSUPPORTED_CAPABILITY", "UNKNOWN_DELIVERY", "ADAPTER_MISSING"}
        if reason not in allowed:
            reason = "UNKNOWN_DELIVERY"
        with self.transaction():
            row = self._claimed(message_id, token)
            state = "retry_wait" if known_unsent else "unknown"
            delay = min(300, 5 * 2 ** min(row["attempts"] - 1, 10))
            self.db.execute("UPDATE messages SET state=?,due=?,error=?,lease_until=NULL WHERE id=?",
                            (state, self.clock() + delay, reason, message_id))
            self.event(state, message_id, {"reason": reason})

    def reconcile_unsent(self, message_id, evidence):
        text(evidence, "evidence")
        with self.transaction():
            row = self.get(message_id)
            if row["state"] != "unknown":
                raise Conflict("RECONCILIATION_REQUIRES_UNKNOWN")
            self.db.execute("UPDATE messages SET state='retry_wait',due=?,error=NULL WHERE id=?", (self.clock(), message_id))
            self.event("retry_proven_safe", message_id, {"evidence": evidence})

    def acknowledge(self, message_id, receipt):
        fields = {"recipient", "generation", "assignment_id", "revision", "turn_id", "action"}
        if not isinstance(receipt, dict) or set(receipt) != fields:
            raise Conflict("INVALID_ACK_FIELDS")
        for key in fields - {"generation", "revision"}:
            text(receipt[key], key)
        for key in ("generation", "revision"):
            positive(receipt[key], key)
        with self.transaction():
            row = self.get(message_id)
            b = row["body"]
            if not self.is_current(row):
                self.event("stale_ack", message_id)
                return {"acknowledged": False, "reason": "STALE_ROUTE_OR_REVISION"}
            if row["state"] not in {"delivered", "acknowledged"}:
                raise Conflict("ACK_REQUIRES_DELIVERY")
            if (receipt["recipient"] != b["recipient"] or receipt["generation"] != row["route_generation"]
                    or receipt["assignment_id"] != b["assignment_id"] or receipt["revision"] != b["revision"]
                    or receipt["turn_id"] != row["receipt"]["turn_id"]):
                raise Conflict("ACK_IDENTITY_MISMATCH")
            if row["ack"] is not None:
                if row["ack"] != receipt:
                    raise Conflict("ACK_CONFLICT")
                return {"acknowledged": True, "duplicate": True}
            self.db.execute("UPDATE messages SET state='acknowledged',ack=? WHERE id=?", (encode(receipt), message_id))
            self.event("acknowledged", message_id)
        return {"acknowledged": True, "duplicate": False}

    def observation(self, message_id, *, cursor=None, terminal=None, result=None):
        with self.transaction():
            row = self.get(message_id)
            if row["state"] not in {"delivered", "acknowledged"}:
                raise Conflict("OBSERVE_REQUIRES_DELIVERY")
            if row["observed"]:
                return None
            if terminal is None:
                self.db.execute("UPDATE messages SET cursor=?,error=NULL WHERE id=?", (encode(cursor), message_id))
                return None
            if terminal not in {"completed", "failed", "cancelled", "timed_out"} or not isinstance(result, dict):
                raise Conflict("INVALID_TERMINAL_RESULT")
            b = row["body"]
            reply = None
            if self.is_current(row) and b["kind"] in {"assignment", "review_request"}:
                h = self.handoff(b["assignment_id"], b["revision"])
                destination = self.session(h["reply_to"]["session_key"])
                event_key = digest([b["assignment_id"], b["revision"], row["receipt"]["task_id"], row["receipt"]["turn_id"], terminal])
                reply_body = {"message_id": "result-" + event_key, "kind": "review_result" if b["kind"] == "review_request" else "result",
                    "plan_id": b["plan_id"], "task_key": b["task_key"], "assignment_id": b["assignment_id"], "revision": b["revision"],
                    "sender": b["recipient"], "sender_generation": row["route_generation"],
                    "recipient": destination["session_key"], "recipient_generation": destination["generation"],
                    "causation_id": message_id, "payload": {"backend_state": terminal, "result": result,
                    "source_receipt": row["receipt"], "business_accepted": False}}
                reply = self._enqueue(reply_body)
            self.db.execute("UPDATE messages SET observed=1,cursor=?,error=NULL WHERE id=?", (encode(cursor), message_id))
            self.event("terminal_observed", message_id, {"state": terminal, "reply_id": reply["id"] if reply else None})
            return reply

    def observation_error(self, message_id):
        with self.transaction():
            row = self.get(message_id)
            if row["error"] != "OBSERVATION_UNAVAILABLE":
                self.db.execute("UPDATE messages SET error='OBSERVATION_UNAVAILABLE' WHERE id=?", (message_id,))
                self.event("observation_unavailable", message_id)
