"""Build a locked, MCP-only Video source projection; no source code is executed."""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import platform
from pathlib import Path
import re

ROOTS = (
    'load_mcp_config', 'check_mcp_tools', 'import_mcp_artifact', 'generate_mcp',
    'regenerate_2k_mcp', 'resume_mcp', 'mcp_status', 'mcp_result',
    'download_mcp_result', 'MCPToolError', 'ClientError', 'redact_message',
    'make_parser', 'STUDIO_VIDEO_CLIENT_CONTRACT',
)
ALLOWED_IMPORTS = {
    'argparse', 'base64', 'datetime', 'decimal', 'hashlib', 'ipaddress', 'json',
    'math', 'os', 'pathlib', 're', 'shutil', 'subprocess', 'tempfile', 'time',
    'typing', 'urllib.error', 'urllib.parse', 'urllib.request', 'dataclasses',
    'channel_contract', 'load_env',
}
H3_CHANGES = (
    ('quality_profile not in {"fasth3-nvfp4", "du-0"}',
     'quality_profile not in {"fasth3-nvfp4"}'),
    ('--quality-profile supports fasth3-nvfp4 or du-0 on route=h3-singularity only',
     '--quality-profile supports fasth3-nvfp4 on route=h3-singularity only'),
    ('choices=["fasth3-nvfp4", "du-0"]', 'choices=["fasth3-nvfp4"]'),
    ('H3-Sing quality profile (fasth3-nvfp4 8-NFE default or du-0 4-NFE fallback)',
     'H3-Sing quality profile (fasth3-nvfp4 8-NFE)'),
)
ENTRY = '''
def main():
    args = make_parser().parse_args()
    try:
        config = load_mcp_config()
        if args.command == "check":
            value = check_mcp_tools(config)
        elif args.command == "import":
            value = import_mcp_artifact(config, args)
        elif args.command == "generate":
            value = generate_mcp(config, args)
        elif args.command == "regenerate-2k":
            value = regenerate_2k_mcp(config, args)
        elif args.command == "status":
            value = mcp_status(config, args.video_task_id)
        elif args.command == "result":
            value = mcp_result(config, args.video_task_id)
            if args.output:
                value = download_mcp_result(config, value, args.output)
        elif args.command == "resume":
            value = resume_mcp(config, args.task_id, args.poll_interval, args.timeout)
        else:
            raise ClientError("Unsupported MCP command")
        print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    except (ClientError, OSError) as error:
        print(json.dumps({"error": error.error}, ensure_ascii=False)
              if isinstance(error, MCPToolError)
              else "VerdantFlare Video failed: " + redact_message(str(error)), file=os.sys.stderr)
        return 1

if __name__ == "__main__":
    raise SystemExit(main())
'''


def project(source: bytes, expected_sha256: str):
    digest = hashlib.sha256(source).hexdigest()
    if not re.fullmatch('[0-9a-f]{64}', expected_sha256) or digest != expected_sha256:
        raise ValueError('SOURCE_LOCK_MISMATCH')
    text = source.decode('utf-8')
    for old, new in H3_CHANGES:
        if text.count(old) != 1:
            raise ValueError('H3_SOURCE_SHAPE_CHANGED')
        text = text.replace(old, new)
    tree = ast.parse(text)
    definitions, imports = {}, []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
            if node.name in definitions:
                raise ValueError('DUPLICATE_DEFINITION')
            definitions[node.name] = node
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if not isinstance(target, ast.Name) or target.id in definitions:
                    raise ValueError('UNSUPPORTED_ASSIGNMENT')
                definitions[target.id] = node
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            imports.append(node)
    if not all(name in definitions for name in ROOTS):
        raise ValueError('MCP_ENTRY_MISSING')
    parser = definitions['make_parser']
    # Remove only the three known statements that expose legacy REST operations.
    removed = []
    for statement in list(parser.body):
        normalized = ast.unparse(statement)
        if (normalized == "sub.add_parser('list')"
                or normalized == "recover_parser = sub.add_parser('recover')"
                or normalized == "recover_parser.add_argument('client_request_id')"):
            removed.append(normalized)
            parser.body.remove(statement)
    if len(removed) != 3:
        raise ValueError('PARSER_SOURCE_SHAPE_CHANGED')

    selected, loaded, todo = set(), set(), list(ROOTS)
    while todo:
        name = todo.pop()
        if name in selected:
            continue
        selected.add(name)
        for node in ast.walk(definitions[name]):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                raise ValueError('NESTED_IMPORT_REQUIRES_REVIEW')
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in {
                    'exec', 'eval', '__import__', 'globals', 'locals'}:
                raise ValueError('DYNAMIC_SOURCE_REQUIRES_REVIEW')
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                loaded.add(node.id)
                if node.id in definitions and node.id not in selected:
                    todo.append(node.id)
    projected_imports, import_names = [], set()
    for node in imports:
        if isinstance(node, ast.ImportFrom) and node.module == '__future__':
            continue
        aliases = [a for a in node.names if (a.asname or a.name.split('.')[0]) in loaded]
        if not aliases:
            continue
        modules = [node.module] if isinstance(node, ast.ImportFrom) else [a.name for a in aliases]
        if any(m not in ALLOWED_IMPORTS for m in modules) or getattr(node, 'level', 0):
            raise ValueError('UNAPPROVED_IMPORT')
        node.names = aliases
        projected_imports.append(node)
        import_names.update(modules)
    if selected & {'Config', 'load_config', 'ensure_mc', 'generate', 'upload_media',
                   'cleanup_expired_uploads', 'recover', 'list_tasks'}:
        raise ValueError('LEGACY_DEPENDENCY')
    body = ast.parse('"""Generated MCP-only Video client; preserve original source separately."""\n'
                     'from __future__ import annotations\n').body
    body += projected_imports
    body += sorted({id(definitions[n]): definitions[n] for n in selected}.values(), key=lambda n: n.lineno)
    body += ast.parse(ENTRY).body
    output = (ast.unparse(ast.Module(body=body, type_ignores=[])) + '\n').encode('utf-8')
    compile(output, '<video-mcp-projection>', 'exec')  # Syntax only, never exec.
    record = {'source_sha256': digest, 'output_sha256': hashlib.sha256(output).hexdigest(),
              'python_version': platform.python_version(), 'python_implementation': platform.python_implementation(),
              'selected_definitions': sorted(selected), 'imports': sorted(import_names),
              'removed_legacy_parser_statements': removed, 'h3_public_profiles': ['fasth3-nvfp4'],
              'scope': 'offline source projection; not installed, packaged or desktop-accepted'}
    return output, record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True, type=Path)
    parser.add_argument('--sha256', required=True)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    try:
        if args.source.is_symlink() or not args.source.is_file() or args.source.stat().st_size > 4 * 1024 * 1024:
            raise ValueError('SOURCE_TYPE_OR_SIZE')
        if any(p.is_symlink() for p in args.source.absolute().parents):
            raise ValueError('SOURCE_SYMLINK')
        if args.output.exists() or args.output.is_symlink():
            raise ValueError('OUTPUT_EXISTS')
        if any(p.is_symlink() for p in args.output.absolute().parents):
            raise ValueError('OUTPUT_SYMLINK')
        output, record = project(args.source.read_bytes(), args.sha256)
        with args.output.open('xb') as stream:
            try:
                stream.write(output)
            except BaseException:
                stream.close()
                args.output.unlink()
                raise
        print(json.dumps(record, sort_keys=True))
        return 0
    except (ValueError, OSError, SyntaxError) as error:
        print(json.dumps({'status': 'rejected', 'code': str(error) if type(error) is ValueError else type(error).__name__}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
