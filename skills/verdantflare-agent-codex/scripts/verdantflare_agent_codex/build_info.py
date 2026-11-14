"""Capture the build identity once, before serving any requests."""
import hashlib
import json
from pathlib import Path


def capture_build(root=None):
    root = Path(root) if root else Path(__file__).parent
    manifest = root / 'build_manifest.json'
    if not manifest.is_file():
        return {'verified': False, 'reason': 'manifest_missing'}
    try:
        raw = manifest.read_bytes()
        files = json.loads(raw)['files']
        actual_names = {p.name for p in root.iterdir() if p.suffix in ('.py', '.mjs')}
        verified = actual_names == set(files) and all(
            '/' not in name and '\\' not in name and
            hashlib.sha256((root / name).read_bytes()).hexdigest() == digest
            for name, digest in files.items())
        return {'sha256': hashlib.sha256(raw).hexdigest(), 'verified': verified,
                'identity_scope': 'module_import', 'protocol': 'thread-recovery-v1'}
    except (OSError, ValueError, KeyError, TypeError):
        return {'verified': False, 'reason': 'manifest_invalid'}


SERVICE_BUILD = capture_build()
