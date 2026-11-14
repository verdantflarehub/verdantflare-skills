import argparse
import asyncio
import json
import os
from pathlib import Path
import sys

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent, CallToolResult
from .engine import Engine
from .errors import BridgeError
from .schema import SCHEMAS


def load_config(path):
    config = json.loads(Path(path).read_text('utf-8'))
    required = {'database', 'codex', 'node', 'extension', 'allowed_roots'}
    if set(config) != required or not isinstance(config['allowed_roots'], list):
        raise ValueError('Configuration must contain database, codex, node, extension and allowed_roots list')
    for value in [config[k] for k in required - {'allowed_roots'}]:
        if not isinstance(value, str) or not Path(value).is_absolute():
            raise ValueError('Configuration paths must be absolute')
    for root in config['allowed_roots']:
        if not isinstance(root, str) or (root != '*' and not Path(root).is_absolute()):
            raise ValueError('allowed_roots entries must be "*" or absolute paths')
    return config


async def serve(engine):
    server = Server('verdantflare-agent-codex')

    @server.list_tools()
    async def list_tools():
        return [Tool(name=name, description=name.replace('_', ' '), inputSchema=schema)
                for name, schema in SCHEMAS.items()]

    @server.call_tool(validate_input=False)
    async def call_tool(name, arguments):
        try:
            result = await engine.call(name, arguments)
            return CallToolResult(content=[TextContent(type='text', text=json.dumps(result, ensure_ascii=False))],
                                  structuredContent=result)
        except BridgeError as error:
            result = error.as_dict()
            return CallToolResult(content=[TextContent(type='text', text=json.dumps(result, ensure_ascii=False))],
                                  structuredContent=result, isError=True)
        except Exception as error:
            result = {'code': 'INTERNAL_ERROR', 'message': type(error).__name__, 'retryable': False}
            return CallToolResult(content=[TextContent(type='text', text=json.dumps(result))], isError=True)

    await engine.start()
    try:
        async with stdio_server() as (reader, writer):
            await server.run(reader, writer, server.create_initialization_options())
    finally:
        await engine.close()


async def run(args):
    if args.export_schemas:
        target = Path(args.export_schemas)
        target.mkdir(parents=True, exist_ok=True)
        for name, schema in SCHEMAS.items():
            (target / (name + '.json')).write_text(json.dumps(schema, indent=2) + '\n', 'utf-8')
        return
    if not args.config:
        raise ValueError('--config is required')
    engine = Engine(load_config(args.config))
    if args.call:
        # CLI writes stay attached until terminal/unknown; don't orphan the owning worker.
        await engine.start()
        try:
            arguments = json.loads(Path(args.arguments).read_text('utf-8')) if args.arguments else {}
            result = await engine.call(args.call, arguments)
            print(json.dumps(result, ensure_ascii=False), flush=True)
            if args.wait and result.get('task_id'):
                while True:
                    state = engine.store.task(result['task_id'])['state']
                    if state in {'completed', 'failed', 'cancelled', 'timed_out', 'interrupted_unknown'}:
                        print(json.dumps(engine.store.result(result['task_id']), ensure_ascii=False), flush=True)
                        break
                    await asyncio.sleep(.25)
        finally:
            await engine.close()
    else:
        await serve(engine)


def main():
    parser = argparse.ArgumentParser(description='Local Codex MCP service')
    parser.add_argument('--config', default=os.environ.get('VERDANTFLARE_AGENT_CODEX_CONFIG'))
    parser.add_argument('--call', choices=SCHEMAS)
    parser.add_argument('--arguments', help='UTF-8 JSON argument file')
    parser.add_argument('--wait', action='store_true')
    parser.add_argument('--export-schemas')
    args = parser.parse_args()
    if args.call in {'codex_task_start', 'codex_task_continue'} and not args.wait:
        parser.error('CLI start/continue requires --wait; MCP calls remain asynchronous')
    try:
        asyncio.run(run(args))
    except (ValueError, BridgeError, OSError) as error:
        print(type(error).__name__ + ': ' + str(error), file=sys.stderr)
        raise SystemExit(1)


if __name__ == '__main__':
    main()
