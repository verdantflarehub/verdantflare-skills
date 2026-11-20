"""Read a private local MCP result snapshot; never connect or change config."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import stat

import config_transaction as cfg
import install_transaction as tx
import tool_name_profile as profile

SCHEMA = 'vf.tool-name-profile-request/v1'
FIELDS = {'schema', 'initialize_sha256', 'tools_sha256', 'observed_prefix'}


def _fingerprint(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_nlink,
            info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _read(path, limit):
    tx.no_links(path)
    cfg.private_file(path)
    before = path.stat()
    tx.require(0 < before.st_size <= limit, 'TOOL_NAME_INPUT_SIZE')
    # O_NONBLOCK prevents a FIFO swapped in after metadata inspection from
    # hanging this read-only command. O_NOFOLLOW rejects a raced-in symlink.
    descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
    with os.fdopen(descriptor, 'rb') as stream:
        opened = os.fstat(stream.fileno())
        tx.require(stat.S_ISREG(opened.st_mode) and _fingerprint(before) == _fingerprint(opened),
                   'TOOL_NAME_INPUT_CHANGED')
        raw = stream.read(limit + 1)
        after = os.fstat(stream.fileno())
    tx.no_links(path)
    cfg.private_file(path)
    tx.require(_fingerprint(before) == _fingerprint(after) == _fingerprint(path.stat())
               and len(raw) == before.st_size, 'TOOL_NAME_INPUT_CHANGED')
    return raw


def _request(raw):
    def pairs(items):
        value = {}
        for key, item in items:
            tx.require(key not in value, 'TOOL_NAME_REQUEST_INVALID')
            value[key] = item
        return value
    try:
        value = json.loads(raw.decode('utf-8'), object_pairs_hook=pairs)
    except (ValueError, UnicodeError, RecursionError):
        raise tx.Conflict('TOOL_NAME_REQUEST_INVALID') from None
    tx.require(type(value) is dict and set(value) == FIELDS and value['schema'] == SCHEMA,
               'TOOL_NAME_REQUEST_INVALID')
    for key in ('initialize_sha256', 'tools_sha256'):
        tx.require(type(value[key]) is str and re.fullmatch('[0-9a-f]{64}', value[key]) is not None,
                   'TOOL_NAME_REQUEST_INVALID')
    tx.require(type(value['observed_prefix']) is str
               and profile.PREFIX.fullmatch(value['observed_prefix']) is not None,
               'TOOL_NAME_HOST_PREFIX_REQUIRED')
    return value


def run(request_path):
    tx.require(os.name == 'posix', 'WINDOWS_PRIVATE_STORAGE_UNVERIFIED')
    path = Path(request_path).absolute()
    tx.no_links(path)
    # Unlike private_directory(), inspection must not create a missing folder.
    info = path.parent.stat()
    tx.require(stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid()
               and stat.S_IMODE(info.st_mode) == 0o700, 'PRIVATE_DIRECTORY_REQUIRED')
    cfg.no_extended_metadata(path.parent)
    tx.require(path.name not in {'initialize-result.json', 'tools-result.json'},
               'TOOL_NAME_REQUEST_INVALID')
    raw = _read(path, 8192)
    value = _request(raw)
    inputs = [(path.parent / 'initialize-result.json', value['initialize_sha256']),
              (path.parent / 'tools-result.json', value['tools_sha256'])]
    results = []
    for source, expected in inputs:
        data = _read(source, profile.MAX_BYTES)
        tx.require(hashlib.sha256(data).hexdigest() == expected, 'TOOL_NAME_INPUT_HASH')
        results.append(data)
    admitted = profile.admit(*results, observed_prefix=value['observed_prefix'])
    # A fresh metadata/read pass detects mutation between the two snapshots.
    for (source, _), data in zip(inputs, results):
        tx.require(_read(source, profile.MAX_BYTES) == data, 'TOOL_NAME_INPUT_CHANGED')
    tx.require(_read(path, 8192) == raw, 'TOOL_NAME_INPUT_CHANGED')
    tx.require(_fingerprint(info) == _fingerprint(path.parent.stat()), 'TOOL_NAME_INPUT_CHANGED')
    return {**admitted.receipt(), 'status': 'local_snapshot_checked',
            'installable': False, 'publisher_signature_verified': False,
            'network_requested': False, 'user_config_changed': False,
            'windows_execution': False}
