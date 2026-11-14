"""Generate the reproducible runtime manifest before packaging or installing."""
import hashlib
import json
from pathlib import Path


def generate(root=None):
    root = Path(root) if root else Path(__file__).parent / 'verdantflare_agent_codex'
    files = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
             for p in sorted(root.iterdir()) if p.suffix in ('.py', '.mjs')}
    content = json.dumps({'files': files}, sort_keys=True, indent=2) + '\n'
    (root / 'build_manifest.json').write_text(content, encoding='utf-8', newline='\n')


if __name__ == '__main__':
    generate()
