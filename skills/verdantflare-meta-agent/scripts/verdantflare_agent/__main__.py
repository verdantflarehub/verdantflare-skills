"""Local-only CLI for the durable collaboration ledger and worker."""
from __future__ import annotations

import argparse
import asyncio
import importlib
import json
from pathlib import Path
import sys

from check_handoff import InvalidHandoff, unique_pairs
from .store import Store, Conflict
from .worker import Worker


def read_json(path):
    raw = Path(path).read_bytes()
    if len(raw) > 262144:
        raise Conflict("INPUT_TOO_LARGE")
    return json.loads(raw.decode("utf-8"), object_pairs_hook=unique_pairs)


def adapters_from_config(config):
    if not isinstance(config, dict) or set(config) != {"services"} or not isinstance(config["services"], dict):
        raise Conflict("INVALID_SERVICE_CONFIG")
    adapters = {}
    for service, settings in config["services"].items():
        if not isinstance(service, str) or not service or not isinstance(settings, dict):
            raise Conflict("INVALID_SERVICE_CONFIG")
        if set(settings) != {"agent", "argv"} or settings["agent"] not in {"codex", "claude", "antigravity", "grok"}:
            raise Conflict("INVALID_SERVICE_CONFIG")
        argv = settings["argv"]
        if not isinstance(argv, list) or not argv or not all(isinstance(x, str) and x for x in argv):
            raise Conflict("INVALID_SERVICE_CONFIG")
        try:
            module = importlib.import_module(".adapters." + settings["agent"], __package__)
        except ModuleNotFoundError:
            raise Conflict("ADAPTER_NOT_IMPLEMENTED") from None
        adapters[service] = module.create(argv)
    return adapters


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--database", required=True)
    parser.add_argument("command", choices=("register", "enqueue", "status", "sessions", "rename", "relink", "ack", "report", "takeover", "migrate", "run"))
    parser.add_argument("--input", help="UTF-8 JSON arguments; run reads service configuration")
    parser.add_argument("--message-id")
    args = parser.parse_args()
    store = None
    try:
        data = read_json(args.input) if args.input else {}
        store = Store(args.database, args.workspace)
        if args.command == "register":
            output = store.register(data)
        elif args.command == "enqueue":
            output = store.enqueue(data)
        elif args.command == "status":
            output = store.get(args.message_id) if args.message_id else store.pending({"queued", "retry_wait", "delivering", "unknown", "delivered", "acknowledged", "expired", "rejected"})
        elif args.command == "sessions":
            output = store.sessions()
        elif args.command == "rename":
            if set(data) != {"session_key", "name"}:
                raise Conflict("INVALID_RENAME_FIELDS")
            output = store.rename(data["session_key"], data["name"])
        elif args.command == "relink":
            if set(data) != {"plan_id", "plan_name", "plan_path", "tasks"}:
                raise Conflict("INVALID_RELINK_FIELDS")
            store.relink(**data)
            output = {"relinked": True}
        elif args.command == "ack":
            output = store.acknowledge(args.message_id, data)
        elif args.command == "report":
            if set(data) != {"terminal", "result"}:
                raise Conflict("INVALID_REPORT_FIELDS")
            output = store.observation(args.message_id, terminal=data["terminal"], result=data["result"])
        elif args.command == "takeover":
            if set(data) != {"endpoint", "expected_generation", "authorization"}:
                raise Conflict("INVALID_TAKEOVER_FIELDS")
            store.takeover(data["endpoint"], data["expected_generation"], data["authorization"])
            output = {"updated": True}
        elif args.command == "migrate":
            if set(data) != {"expected_generation"}:
                raise Conflict("INVALID_MIGRATION_FIELDS")
            store.migrate(args.message_id, data["expected_generation"])
            output = {"migrated": True}
        else:
            adapters = adapters_from_config(data)
            asyncio.run(Worker(store, adapters).run())
            return 0
        print(json.dumps(output, ensure_ascii=False, allow_nan=False))
        return 0
    except (Conflict, InvalidHandoff) as error:
        print(json.dumps({"error": str(error)}, ensure_ascii=False), file=sys.stderr)
        return 1
    except (OSError, ValueError, TypeError, KeyError, RecursionError):
        print('{"error":"INVALID_INPUT_OR_LOCAL_STATE"}', file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    finally:
        if store:
            store.close()


if __name__ == "__main__":
    raise SystemExit(main())
