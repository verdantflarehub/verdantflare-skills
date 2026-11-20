"""Non-secret WorkBuddy remote-MCP configuration for an admitted connection.

Pure planning and explicit-root byte transactions only. The caller authenticates
the connection descriptor, publisher, host version and activity before entry.
Passing an URL here is not source admission, OAuth or desktop verification.
No network, discovery, credential acquisition or private OAuth-store access.
Inherited private byte backups may include secrets already in the config file.
"""
from __future__ import annotations

import ipaddress
import re
from urllib.parse import urlsplit

import config_transaction as transaction
import host_config as config

TIMEOUT_MS = 30000
MAX_URL = 4096
FIELDS = {'url', 'timeout', 'disabled'}


class Conflict(ValueError):
    """Constant diagnostics only; never echo connection/configuration values."""


def require(condition, code):
    if not condition:
        raise Conflict(code)


def validate_url(url):
    """Check canonical URL syntax; deliberately makes no authority/trust claim."""
    require(type(url) is str and 0 < len(url) <= MAX_URL and url.isascii()
            and not any(ord(c) <= 32 or ord(c) == 127 for c in url)
            and not any(c in url for c in ('\\', '$', '%', '?', '#')),
            'WORKBUDDY_URL_INVALID')
    try:
        parsed = urlsplit(url)
        host, port = parsed.hostname, parsed.port
        require(parsed.scheme == 'https' and host and host == host.lower()
                and parsed.username is None and parsed.password is None
                and not parsed.query and not parsed.fragment,
                'WORKBUDDY_URL_INVALID')
        # DNS spelling or canonical IP spelling, without aliases/trailing dots.
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            # WHATWG URL consumers interpret a numeric/hex final label as an
            # IPv4 number, including shortened, octal and integer aliases.
            # Never admit those spellings as ordinary DNS after IPv4 failed.
            require(not re.fullmatch(r'(?:[0-9]+|0[xX][0-9a-fA-F]*)', host.rsplit('.', 1)[-1]),
                    'WORKBUDDY_URL_INVALID')
            require(len(host) <= 253 and all(re.fullmatch(
                r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', label)
                for label in host.split('.')), 'WORKBUDDY_URL_INVALID')
            authority = host
        else:
            require(str(address) == host, 'WORKBUDDY_URL_INVALID')
            authority = '[' + host + ']' if address.version == 6 else host
        if port is not None:
            require(0 < port <= 65535 and port != 443, 'WORKBUDDY_URL_INVALID')
            authority += ':' + str(port)
        require(parsed.netloc == authority
                and re.fullmatch(r'/mcp/desktop/[A-Za-z0-9._~-]+', parsed.path)
                and parsed.path.rsplit('/', 1)[1] not in {'.', '..'}
                and url == 'https://' + authority + parsed.path,
                'WORKBUDDY_URL_INVALID')
    except ValueError as error:
        if isinstance(error, Conflict):
            raise
        raise Conflict('WORKBUDDY_URL_INVALID') from None


def desired_connection(url, *, admitted_url):
    """The exact admitted URL is supplied by the upstream admission boundary."""
    validate_url(admitted_url)
    validate_url(url)
    require(url == admitted_url, 'WORKBUDDY_CONNECTION_MISMATCH')
    return {'url': url, 'timeout': TIMEOUT_MS, 'disabled': False}


def validate_desired(value, *, admitted_url):
    require(type(value) is dict and set(value) == FIELDS,
            'WORKBUDDY_DESIRED_INVALID')
    expected = desired_connection(value['url'], admitted_url=admitted_url)
    require(type(value['timeout']) is int and value['timeout'] == expected['timeout']
            and value['disabled'] is False, 'WORKBUDDY_DESIRED_INVALID')


def plan_update(raw, desired, *, admitted_url, expected_config_sha256,
                expected_owned_sha256):
    validate_url(admitted_url)
    if desired is not None:
        validate_desired(desired, admitted_url=admitted_url)
    return config.plan_update(raw, desired,
                              expected_config_sha256=expected_config_sha256,
                              expected_owned_sha256=expected_owned_sha256)


class _Planner:
    MAX_BYTES = config.MAX_BYTES
    inspect = staticmethod(config.inspect)
    digest = staticmethod(config.digest)

    def __init__(self, admitted_url):
        self._admitted_url = admitted_url

    def plan_update(self, raw, desired, **cas):
        return plan_update(raw, desired, admitted_url=self._admitted_url, **cas)


class ConfigFile(transaction.ConfigFile):
    """Existing JSON CAS/recovery journal, in a caller-specified isolated root.

    Does not grant admission to a real user root. No OAuth state is stored here.
    Recovery preserves original bytes even when their old target differs from
    the new connection; compatibility and identity must be checked before use.
    """

    def __init__(self, root, *, admitted_url):
        validate_url(admitted_url)
        self.planner = _Planner(admitted_url)
        super().__init__(root, 'mcp.json')
