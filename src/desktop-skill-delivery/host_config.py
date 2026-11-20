"""Lossless JSON planning for one installer-owned MCP entry.

Pure byte transformations: no filesystem, host discovery, secrets persistence,
release admission, or support claims. The transaction caller must compare the
whole-file digest again immediately before committing and retain recovery data.
Unsupported host formats must use their own verified adapters.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json

MAX_BYTES = 1024 * 1024
MAX_DEPTH = 64
SERVER_KEY = 'vf-studio'


class ConfigConflict(ValueError):
    """Only constant codes, never configuration content."""


def require(value, code):
    if not value:
        raise ConfigConflict(code)


def digest(raw):
    return None if raw is None else hashlib.sha256(raw).hexdigest()


@dataclass(repr=False)
class Node:
    start: int
    end: int
    members: list = field(default_factory=list)
    is_object: bool = False


@dataclass(repr=False)
class Member:
    key: str
    start: int
    value: Node
    end: int


class Parser:
    """Retain character spans; validate every node, including unrelated data."""

    def __init__(self, raw):
        require(type(raw) is bytes and len(raw) <= MAX_BYTES, 'CONFIG_SIZE_OR_TYPE')
        try:
            self.text = raw.decode('utf-8')
        except UnicodeDecodeError:
            raise ConfigConflict('CONFIG_INVALID_UTF8') from None
        self.decoder = json.JSONDecoder(parse_constant=self.nonfinite)
        self.pos = 0

    @staticmethod
    def nonfinite(_):
        raise ConfigConflict('CONFIG_NONFINITE_NUMBER')

    def skip(self):
        while self.pos < len(self.text) and self.text[self.pos] in ' \t\r\n':
            self.pos += 1

    def take(self, char):
        require(self.text[self.pos:self.pos+1] == char, 'CONFIG_INVALID_JSON')
        self.pos += 1

    def parse(self):
        try:
            root = self.node(0)
            self.skip()
            require(self.pos == len(self.text), 'CONFIG_INVALID_JSON')
            require(root.is_object, 'CONFIG_ROOT_NOT_OBJECT')
            return root
        except (json.JSONDecodeError, UnicodeError, OverflowError, RecursionError):
            raise ConfigConflict('CONFIG_INVALID_JSON') from None
        except ValueError as error:
            if isinstance(error, ConfigConflict):
                raise
            raise ConfigConflict('CONFIG_INVALID_JSON') from None

    def node(self, depth):
        require(depth <= MAX_DEPTH, 'CONFIG_TOO_DEEP')
        self.skip()
        start = self.pos
        char = self.text[self.pos:self.pos+1]
        if char == '{':
            self.pos += 1
            members = []
            seen = set()
            self.skip()
            if self.text[self.pos:self.pos+1] != '}':
                while True:
                    self.skip()
                    member_start = self.pos
                    require(self.text[self.pos:self.pos+1] == '"', 'CONFIG_INVALID_JSON')
                    key, self.pos = self.decoder.raw_decode(self.text, self.pos)
                    require(not any(0xD800 <= ord(c) <= 0xDFFF for c in key), 'CONFIG_INVALID_UNICODE')
                    require(key not in seen, 'CONFIG_DUPLICATE_KEY')
                    seen.add(key)
                    self.skip()
                    self.take(':')
                    value = self.node(depth+1)
                    members.append(Member(key, member_start, value, value.end))
                    self.skip()
                    if self.text[self.pos:self.pos+1] != ',':
                        break
                    self.pos += 1
            self.take('}')
            return Node(start, self.pos, members, True)
        if char == '[':
            self.pos += 1
            self.skip()
            if self.text[self.pos:self.pos+1] != ']':
                while True:
                    self.node(depth+1)
                    self.skip()
                    if self.text[self.pos:self.pos+1] != ',':
                        break
                    self.pos += 1
            self.take(']')
            return Node(start, self.pos)
        value, self.pos = self.decoder.raw_decode(self.text, self.pos)
        # JSON permits huge decimal exponents; Python would silently yield inf.
        if isinstance(value, float):
            import math
            require(math.isfinite(value), 'CONFIG_NONFINITE_NUMBER')
        if isinstance(value, str):
            require(not any(0xD800 <= ord(c) <= 0xDFFF for c in value), 'CONFIG_INVALID_UNICODE')
        return Node(start, self.pos)


def member(node, name):
    return next((m for m in node.members if m.key == name), None)


def inspect(raw):
    parser = Parser(raw if raw is not None else b'{}')
    root = parser.parse()
    container = member(root, 'mcpServers')
    require(container is None or container.value.is_object, 'MCP_SERVERS_NOT_OBJECT')
    entry = None if container is None else member(container.value, SERVER_KEY)
    node_bytes = None if entry is None else parser.text[entry.value.start:entry.value.end].encode('utf-8')
    return parser, root, container, entry, node_bytes


@dataclass(frozen=True, repr=False)
class ConfigPlan:
    before_sha256: str | None
    after: bytes | None
    owned_before_sha256: str | None
    owned_after_sha256: str | None
    changed: bool

    def receipt(self):
        """Hashes only; callers must not serialize the in-memory plan itself."""
        return {'before_sha256': self.before_sha256, 'after_sha256': digest(self.after),
                'owned_before_sha256': self.owned_before_sha256,
                'owned_after_sha256': self.owned_after_sha256, 'changed': self.changed,
                'disk_written': False, 'host_verified': False}


def plan_update(raw, desired, *, expected_config_sha256, expected_owned_sha256):
    """Set/delete mcpServers.vf-studio, checking whole-file and ownership CAS.

    None for raw denotes a missing file. None for desired deletes the owned key.
    None for expected_owned_sha256 means no prior ownership: never adopt a key.
    desired is a host-specific object supplied by the caller after credential
    policy validation, not an untrusted download or a credential acquisition API.
    """
    require(raw is None or type(raw) is bytes, 'CONFIG_SIZE_OR_TYPE')
    require(digest(raw) == expected_config_sha256, 'CONFIG_CHANGED')
    parser, root, container, entry, owned_raw = inspect(raw)
    require(digest(owned_raw) == expected_owned_sha256, 'OWNERSHIP_CONFLICT')
    require(desired is None or type(desired) is dict, 'DESIRED_NOT_OBJECT')
    target = None
    if desired is not None:
        try:
            target = json.dumps(desired, ensure_ascii=False, allow_nan=False, separators=(',', ':'))
            target_raw = target.encode('utf-8')
            Parser(target_raw).parse()
        except (TypeError, ValueError, UnicodeError, RecursionError):
            raise ConfigConflict('DESIRED_INVALID_JSON') from None
    text = parser.text
    if entry is None and desired is None:
        after = raw
    elif entry is not None and desired is not None:
        # Preserve original formatting when the owned value is semantically equal.
        if json.dumps(json.loads(owned_raw), sort_keys=True) == json.dumps(json.loads(target), sort_keys=True):
            after = raw
        else:
            after = (text[:entry.value.start] + target + text[entry.value.end:]).encode('utf-8')
    elif entry is not None:
        members = container.value.members
        index = members.index(entry)
        if len(members) == 1:
            start, end = entry.start, entry.end
        elif index == 0:
            start, end = entry.start, members[1].start
        else:
            start, end = members[index-1].end, entry.end
        after = (text[:start] + text[end:]).encode('utf-8')
    else:
        parent = root if container is None else container.value
        addition = json.dumps(SERVER_KEY) + ':' + target
        if container is None:
            addition = '"mcpServers":{' + addition + '}'
        insert = parent.members[-1].end if parent.members else parent.start+1
        after = (text[:insert] + (',' if parent.members else '') + addition + text[insert:]).encode('utf-8')
    _, _, _, _, after_owned = inspect(after)
    return ConfigPlan(digest(raw), after, digest(owned_raw), digest(after_owned), after != raw)
