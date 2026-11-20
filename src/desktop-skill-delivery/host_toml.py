"""In-memory, lossless TOML plans for Codex's one installer-owned MCP node.

No host discovery, disk writes, credentials, helper execution or desktop claims.
TOMLKit belongs to the pinned D03 dependency closure, not the host environment.
"""
from __future__ import annotations

import json
import math
import tomllib

from host_config import ConfigConflict, ConfigPlan, digest, require

MAX_BYTES = 1024 * 1024
MAX_DEPTH = 64
SERVER_KEY = 'vf-studio'
LIBRARY_VERSION = '0.15.2'


def _library():
    try:
        import tomlkit
    except ImportError:
        raise ConfigConflict('TOML_DEPENDENCY_REQUIRED') from None
    require(tomlkit.__version__ == LIBRARY_VERSION, 'TOML_DEPENDENCY_VERSION_UNVERIFIED')
    return tomlkit


def _normalized(value, depth=0):
    require(depth <= MAX_DEPTH, 'TOML_TOO_DEEP')
    if isinstance(value, dict):
        return ('mapping', tuple((k, _normalized(v, depth + 1)) for k, v in sorted(value.items())))
    if isinstance(value, list):
        return ('list', tuple(_normalized(v, depth + 1) for v in value))
    if isinstance(value, float):
        return ('float', value.hex())
    # TOML dates and times are valid unrelated settings; retain their types.
    return (type(value).__name__, value)


def _parse(raw):
    require(raw is None or type(raw) is bytes and len(raw) <= MAX_BYTES, 'TOML_SIZE_OR_TYPE')
    library = _library()
    try:
        text = '' if raw is None else raw.decode('utf-8')
        parsed = tomllib.loads(text)
        _normalized(parsed)
        document = library.parse(text)
        require(library.dumps(document) == text, 'TOML_LAYOUT_NOT_LOSSLESS')
        require(_normalized(document.unwrap()) == _normalized(parsed), 'TOML_PARSER_DISAGREEMENT')
    except ConfigConflict:
        raise
    except (ValueError, TypeError, KeyError, UnicodeError, OverflowError, RecursionError, library.exceptions.TOMLKitError):
        raise ConfigConflict('TOML_INVALID_DOCUMENT') from None
    servers = parsed.get('mcp_servers')
    require(servers is None or type(servers) is dict, 'TOML_MCP_SERVERS_NOT_TABLE')
    value = None if servers is None else servers.get(SERVER_KEY)
    require(value is None or type(value) is dict, 'TOML_SERVER_NOT_TABLE')
    item = None if servers is None or SERVER_KEY not in servers else document['mcp_servers'][SERVER_KEY]
    if item is None:
        owned = None
    else:
        from tomlkit.container import OutOfOrderTableProxy
        if isinstance(item, OutOfOrderTableProxy):
            # This pinned library exposes no public serializer for a proxy.
            # Bind every physical table fragment, retaining comments/order and
            # using an unambiguous list rather than concatenating fragments.
            require(type(item._tables) is list and all(isinstance(part, library.items.Table)
                    for part in item._tables), 'TOML_SPLIT_LAYOUT_UNSUPPORTED')
            fragments = [part.as_string() for part in item._tables]
        else:
            require(isinstance(item, (library.items.Table, library.items.InlineTable)), 'TOML_SERVER_NOT_TABLE')
            fragments = [item.as_string()]
        owned = json.dumps(fragments, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    return library, document, parsed, owned


def inspect(raw):
    """Return ownership bytes only; callers must not log configuration content."""
    return _parse(raw)[-1]


def _desired(desired):
    require(desired is None or type(desired) is dict, 'TOML_DESIRED_NOT_OBJECT')
    if desired is None:
        return

    def shape(value, depth=0):
        require(depth <= MAX_DEPTH, 'TOML_DESIRED_TOO_DEEP')
        if type(value) is dict:
            require(all(type(k) is str for k in value), 'TOML_DESIRED_INVALID')
            for item in value.values():
                shape(item, depth + 1)
        elif type(value) is list:
            for item in value:
                shape(item, depth + 1)
        else:
            require(type(value) in (str, bool, int, float), 'TOML_DESIRED_INVALID')
            require(type(value) is not int or -(2**63) <= value < 2**63, 'TOML_DESIRED_INVALID')
            require(type(value) is not float or math.isfinite(value), 'TOML_DESIRED_INVALID')
    try:
        shape(desired)
        raw = json.dumps(desired, allow_nan=False, ensure_ascii=False).encode('utf-8')
        require(len(raw) <= MAX_BYTES, 'TOML_DESIRED_SIZE')
    except ConfigConflict:
        raise
    except (ValueError, TypeError, UnicodeError, RecursionError, OverflowError):
        raise ConfigConflict('TOML_DESIRED_INVALID') from None


def _foreign(parsed):
    result = dict(parsed)
    if 'mcp_servers' in result:
        servers = dict(result['mcp_servers'])
        servers.pop(SERVER_KEY, None)
        if servers:
            result['mcp_servers'] = servers
        else:
            result.pop('mcp_servers')
    return _normalized(result)


def plan_update(raw, desired, *, expected_config_sha256, expected_owned_sha256):
    """Whole-file and VF-node CAS; never adopt an existing unowned node."""
    require(raw is None or type(raw) is bytes, 'TOML_SIZE_OR_TYPE')
    require(digest(raw) == expected_config_sha256, 'CONFIG_CHANGED')
    library, document, before, owned = _parse(raw)
    require(digest(owned) == expected_owned_sha256, 'OWNERSHIP_CONFLICT')
    _desired(desired)
    previous = before.get('mcp_servers', {}).get(SERVER_KEY)
    if previous is None and desired is None or previous is not None and desired is not None \
            and _normalized(previous) == _normalized(desired):
        return ConfigPlan(digest(raw), raw, digest(owned), digest(owned), False)
    try:
        if desired is None:
            del document['mcp_servers'][SERVER_KEY]
        else:
            if 'mcp_servers' not in document:
                document['mcp_servers'] = library.table(is_super_table=True)
            parent = document['mcp_servers']
            original = parent.get(SERVER_KEY)
            inline = isinstance(parent, library.items.InlineTable) or isinstance(original, library.items.InlineTable)
            if original is not None:
                # Replacing an OutOfOrderTableProxy can drop sibling dotted
                # keys in a later explicit parent fragment (TOMLKit 0.15.2).
                # Delete only the owned node, then reacquire the current parent;
                # the old proxy may no longer describe the document's fragments.
                del parent[SERVER_KEY]
                parent = document['mcp_servers']
            item = library.inline_table() if inline else library.table()
            for key, value in desired.items():
                item[key] = value
            parent[SERVER_KEY] = item
        after = library.dumps(document).encode('utf-8')
        require(len(after) <= MAX_BYTES, 'TOML_RESULT_SIZE')
        _, _, checked, after_owned = _parse(after)
        actual = checked.get('mcp_servers', {}).get(SERVER_KEY)
        require((actual is None if desired is None else _normalized(actual) == _normalized(desired)),
                'TOML_TARGET_MISMATCH')
        require(_foreign(before) == _foreign(checked), 'TOML_FOREIGN_CONFIGURATION_CHANGED')
    except ConfigConflict:
        raise
    except (ValueError, TypeError, KeyError, UnicodeError, OverflowError, RecursionError, library.exceptions.TOMLKitError):
        raise ConfigConflict('TOML_EDIT_UNSUPPORTED') from None
    return ConfigPlan(digest(raw), after, digest(owned), digest(after_owned), after != raw)
