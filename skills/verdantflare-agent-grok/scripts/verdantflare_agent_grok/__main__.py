"""Stdio MCP server and one-shot CLI entry point for verdantflare-agent-grok."""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from mcp.server import Server
from mcp.server.stdio import stdio_server
import mcp.types as types

from .engine import Engine
from .errors import BridgeError
from .schema import SCHEMAS
from .store import TERMINAL


async def serve(config: dict) -> None:
    engine = Engine(config)
    await engine.start()
    server = Server("verdantflare-agent-grok")

    @server.list_tools()
    async def list_tools():
        return [
            types.Tool(
                name=name,
                description=f"Grok CLI + AFK Pilot task tool: {name}",
                inputSchema=schema,
            )
            for name, schema in SCHEMAS.items()
        ]

    @server.call_tool(validate_input=False)
    async def call_tool(name: str, arguments: dict | None):
        try:
            result = await engine.call(name, arguments or {})
            return types.CallToolResult(
                content=[types.TextContent(type="text", text=json.dumps(result, ensure_ascii=False))],
                structuredContent=result,
            )
        except BridgeError as error:
            result = error.as_dict()
            return types.CallToolResult(
                content=[types.TextContent(type="text", text=json.dumps(result, ensure_ascii=False))],
                structuredContent=result,
                isError=True,
            )
        except Exception as error:
            result = {"code": "INTERNAL_ERROR", "message": type(error).__name__, "retryable": False}
            return types.CallToolResult(
                content=[types.TextContent(type="text", text=json.dumps(result))],
                isError=True,
            )

    try:
        async with stdio_server() as (reader, writer):
            await server.run(reader, writer, server.create_initialization_options())
    finally:
        await engine.close()


async def cli_call(config: dict, tool: str, arguments: dict, wait: bool) -> int:
    engine = Engine(config)
    await engine.start()
    try:
        result = await engine.call(tool, arguments)
        if wait and result.get("task_id"):
            while True:
                current = engine.store.task(result["task_id"])
                if current["state"] in TERMINAL or current["state"] in ("waiting_input", "waiting_approval", "interrupted_unknown"):
                    result = await engine.call("grok_task_status", {"task_id": result["task_id"]})
                    break
                await asyncio.sleep(0.3)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except BridgeError as error:
        print(json.dumps({"error": error.as_dict()}, ensure_ascii=False))
        return 1
    finally:
        await engine.close()


def main() -> None:
    parser = argparse.ArgumentParser(prog="verdantflare-agent-grok")
    parser.add_argument("--config", required=True)
    parser.add_argument("--call")
    parser.add_argument("--arguments", default="{}")
    parser.add_argument("--wait", action="store_true")
    args = parser.parse_args()
    config = json.loads(Path(args.config).read_text(encoding="utf-8"))
    if args.call:
        raw = args.arguments if args.arguments.lstrip().startswith("{") else Path(args.arguments).read_text(encoding="utf-8")
        raise SystemExit(asyncio.run(cli_call(config, args.call, json.loads(raw), args.wait)))
    asyncio.run(serve(config))


if __name__ == "__main__":
    main()
