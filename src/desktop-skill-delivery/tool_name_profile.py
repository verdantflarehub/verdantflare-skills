"""Read-only admission of an explicitly selected portable-v1 MCP directory.

Consumes result JSON, not JSON-RPC envelopes. The caller must authenticate the
transport, correlate responses and observe the actual host prefix. This module
does not enable a server feature, write config, send a request or invoke a tool.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import re

PROFILE = 'portable-v1'
PROFILE_KEY = 'verdantflare.ai/tool-name-profile'
DIGEST_KEY = 'verdantflare.ai/tool-name-map-digest'
MARKER = 'VF canonical tool: '
CANONICAL = re.compile(r'[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*', re.ASCII)
PORTABLE = re.compile(r'[a-z0-9_]{1,48}', re.ASCII)
PREFIX = re.compile(r'[A-Za-z0-9_-]{1,63}', re.ASCII)
MAX_BYTES = 8 * 1024 * 1024
MAX_TOOLS = 2048


class ProfileError(ValueError):
    """Fixed error codes only; never include remote content in diagnostics."""


def require(ok, code):
    if not ok:
        raise ProfileError(code)


def _result(raw):
    require(type(raw) is bytes and 0 < len(raw) <= MAX_BYTES, 'TOOL_NAME_RESULT_SIZE')

    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, 'TOOL_NAME_RESULT_JSON')
            result[key] = value
        return result

    def constant(_):
        raise ProfileError('TOOL_NAME_RESULT_JSON')

    try:
        value = json.loads(raw.decode('utf-8'), object_pairs_hook=pairs, parse_constant=constant)
    except (ValueError, UnicodeError, RecursionError):
        raise ProfileError('TOOL_NAME_RESULT_JSON') from None
    require(type(value) is dict, 'TOOL_NAME_RESULT_SHAPE')
    # Reject oversized/deep trees without recursive validation or logging data.
    stack, count = [(value, 0)], 0
    while stack:
        item, depth = stack.pop()
        count += 1
        require(depth <= 64 and count <= 200000, 'TOOL_NAME_RESULT_LIMIT')
        if type(item) is dict:
            require(all(not any(0xD800 <= ord(c) <= 0xDFFF for c in k) for k in item),
                    'TOOL_NAME_RESULT_JSON')
            stack.extend((child, depth + 1) for child in item.values())
        elif type(item) is list:
            stack.extend((child, depth + 1) for child in item)
        elif type(item) is str:
            require(not any(0xD800 <= ord(c) <= 0xDFFF for c in item), 'TOOL_NAME_RESULT_JSON')
        elif type(item) is float:
            require(math.isfinite(item), 'TOOL_NAME_RESULT_JSON')
    return value


def portable_name(canonical):
    require(type(canonical) is str and CANONICAL.fullmatch(canonical) is not None,
            'TOOL_NAME_CANONICAL_INVALID')
    name = canonical.replace('_', '_u').replace('.', '__')
    require(PORTABLE.fullmatch(name) is not None, 'TOOL_NAME_PORTABLE_LIMIT')
    return name


def _canonical(description):
    require(type(description) is str and description.count(MARKER) == 1,
            'TOOL_NAME_MARKER_INVALID')
    position = description.index(MARKER)
    require(position == 0 or description[position - 1].isspace(), 'TOOL_NAME_MARKER_INVALID')
    suffix = description[position + len(MARKER):]
    require(suffix.endswith('.'), 'TOOL_NAME_MARKER_INVALID')
    name = suffix[:-1]
    require(CANONICAL.fullmatch(name) is not None, 'TOOL_NAME_MARKER_INVALID')
    return name


@dataclass(frozen=True, repr=False)
class Directory:
    """One admitted snapshot. Never reuse it after discovery/context changes."""
    mapping_sha256: str
    entries: tuple[tuple[str, str], ...]
    observed_prefix: str

    def resolve(self, canonical):
        require(type(canonical) is str and CANONICAL.fullmatch(canonical) is not None,
                'TOOL_NAME_CANONICAL_INVALID')
        for business, actual in self.entries:
            if business == canonical:
                return actual
        raise ProfileError('TOOL_NAME_NOT_DISCOVERED')

    def receipt(self):
        return {'schema': 'vf.tool-name-profile-check/v1', 'profile': PROFILE,
                'mapping_sha256': self.mapping_sha256, 'tool_count': len(self.entries),
                'max_visible_name_length': max((len(self.observed_prefix) + len(name)
                                                for _, name in self.entries), default=0),
                'server_authenticated': False, 'host_verified': False,
                'configuration_written': False, 'tool_invoked': False,
                'desktop_acceptance': False, 'mcp_acceptance': False}


def admit(initialize_result, list_result, *, observed_prefix):
    """Verify complete result bytes and return only the explicit exact map.

    Prefix must be the caller's separately observed wrapper prefix, including
    separators. A nonempty value is required; supplying one is not host proof.
    """
    require(type(observed_prefix) is str and PREFIX.fullmatch(observed_prefix) is not None,
            'TOOL_NAME_HOST_PREFIX_REQUIRED')
    initial, directory = _result(initialize_result), _result(list_result)
    for response in (initial, directory):
        meta = response.get('_meta')
        require(type(meta) is dict and meta.get(PROFILE_KEY) == PROFILE,
                'TOOL_NAME_PROFILE_UNSUPPORTED')
    digest = directory['_meta'].get(DIGEST_KEY)
    require(type(digest) is str and re.fullmatch(r'sha256:[0-9a-f]{64}', digest) is not None,
            'TOOL_NAME_MAP_DIGEST_INVALID')
    tools = directory.get('tools')
    require(type(tools) is list and len(tools) <= MAX_TOOLS, 'TOOL_NAME_DIRECTORY_INVALID')
    # Pagination is not a complete directory and cannot establish a full map.
    require('nextCursor' not in directory, 'TOOL_NAME_DIRECTORY_INCOMPLETE')
    entries, canonical_seen, portable_seen = [], set(), set()
    for tool in tools:
        require(type(tool) is dict and type(tool.get('name')) is str,
                'TOOL_NAME_DIRECTORY_INVALID')
        canonical = _canonical(tool.get('description'))
        expected = portable_name(canonical)
        require(tool['name'] == expected, 'TOOL_NAME_MAPPING_MISMATCH')
        require(canonical not in canonical_seen and expected not in portable_seen,
                'TOOL_NAME_MAPPING_DUPLICATE')
        require(len(observed_prefix) + len(expected) <= 64, 'TOOL_NAME_HOST_LIMIT')
        canonical_seen.add(canonical)
        portable_seen.add(expected)
        entries.append((canonical, expected))
    entries.sort()
    encoded = ''.join(canonical + '\t' + portable + '\n' for canonical, portable in entries).encode('ascii')
    actual_digest = 'sha256:' + hashlib.sha256(encoded).hexdigest()
    require(digest == actual_digest, 'TOOL_NAME_MAP_DIGEST_MISMATCH')
    return Directory(actual_digest, tuple(entries), observed_prefix)
