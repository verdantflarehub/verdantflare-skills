"""Pure planning for the pinned Harness Settings consumer, never a wire client.

The caller must establish identity, writable scope, and native observations.
Comparing caller-supplied evidence here does not authenticate its source.
No YAML, filesystem, credentials, network, or desktop operations occur here.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
import hashlib
import json
import re
from urllib.parse import urlsplit

import host_config as json_config

KEY = 'vf-studio'
PATH = ['servers', KEY]
DMG_SHA256 = '65ec0268d77b8e65786d51fd8b89bed13040f0e53e547d15ee2ca2e013ebeb40'
SETTINGS_SHA256 = '041a2682ed77a7025ceb9bf659b80db8b788c338cd1aa644f0bcc6bc122a4091'
SAFE_INT = 2 ** 53 - 1
DEFAULTS = {'command': '', 'args': [], 'env': {}, 'cwd': '', 'startupTimeoutMs': 30000,
            'reconnect': {'enabled': True, 'initialDelayMs': 500, 'maxDelayMs': 30000, 'maxAttempts': 10}}


class Conflict(ValueError):
    """Only constant diagnostics, never values from the descriptor."""


def require(condition, code):
    if not condition:
        raise Conflict(code)


def encode(value):
    try:
        raw = json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True,
                         separators=(',', ':')).encode('utf-8')
        json_config.Parser(b'{"value":' + raw + b'}').parse()
        return raw
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise Conflict('DEEPSEEK_INVALID_JSON') from None


def digest(value):
    return hashlib.sha256(encode(value)).hexdigest()


def read(raw):
    require(type(raw) is bytes, 'DEEPSEEK_DESCRIPTOR_BYTES_REQUIRED')
    try:
        json_config.Parser(raw).parse()
        return json.loads(raw)
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise Conflict('DEEPSEEK_INVALID_DESCRIPTOR') from None


@dataclass(frozen=True, repr=False)
class Identity:
    product: str
    version: str
    platform: str
    arch: str
    package_sha256: str
    settings_sha256: str
    backend_generation: str
    profile: str
    entry: str

    def validate(self):
        require((self.product, self.version, self.platform, self.arch,
                 self.package_sha256, self.settings_sha256) ==
                ('com.deepseek.dsh', '0.2.2-rc.3', 'macos', 'arm64', DMG_SHA256, SETTINGS_SHA256),
                'DEEPSEEK_PROFILE_UNVERIFIED')
        for value in (self.backend_generation, self.profile, self.entry):
            require(type(value) is str and 0 < len(value) <= 512
                    and not any(ord(c) < 32 or ord(c) == 127 for c in value), 'DEEPSEEK_IDENTITY_INVALID')
        encode(asdict(self))


def desired_connection(url, credential_ref):
    """Only native references; this does not create or resolve the credential."""
    require(type(url) is str and len(url) <= 4096 and url.isascii()
            and not any(ord(c) <= 32 or ord(c) == 127 for c in url)
            and not any(c in url for c in ('\\', '$', '?', '#', '%')), 'DEEPSEEK_URL_INVALID')
    try:
        parsed = urlsplit(url)
        require(parsed.scheme == 'https' and parsed.hostname and parsed.username is None
                and parsed.password is None and parsed.path and not parsed.query and not parsed.fragment
                and parsed.port != 0, 'DEEPSEEK_URL_INVALID')
    except ValueError:
        raise Conflict('DEEPSEEK_URL_INVALID') from None
    require(type(credential_ref) is str and len(credential_ref) <= 128
            and re.fullmatch('[A-Za-z_][A-Za-z0-9_]*', credential_ref), 'DEEPSEEK_CREDENTIAL_REFERENCE_REQUIRED')
    return {'transport': 'streamable-http', 'url': url, 'enabled': True,
            'headers': {'Authorization': 'Bearer ${credential:' + credential_ref + '}'}}


def validate_desired(value):
    require(type(value) is dict and set(value) == {'transport', 'url', 'enabled', 'headers'}
            and type(value['headers']) is dict and set(value['headers']) == {'Authorization'},
            'DEEPSEEK_DESIRED_INVALID')
    header = value['headers']['Authorization']
    match = re.fullmatch(r'Bearer \$\{credential:([A-Za-z_][A-Za-z0-9_]*)\}', header) if type(header) is str else None
    require(match is not None, 'DEEPSEEK_CREDENTIAL_REFERENCE_REQUIRED')
    expected = desired_connection(value['url'], match[1])
    require(encode(expected) == encode(value), 'DEEPSEEK_DESIRED_INVALID')


def effective_matches(node, expected):
    require(type(node) is dict, 'DEEPSEEK_EFFECTIVE_CONFLICT')
    require(all(k in node and encode(node[k]) == encode(v) for k, v in expected.items()),
            'DEEPSEEK_EFFECTIVE_CONFLICT')
    for key in set(node) - set(expected):
        require(key in DEFAULTS and encode(node[key]) == encode(DEFAULTS[key]), 'DEEPSEEK_EFFECTIVE_CONFLICT')


def normalize_schema(schema):
    """Decode pinned Schemastery wire refs without evaluating schema callbacks.

    Form UIDs change on describe; only the reachable semantic tree is compared.
    Tree-shaped inputs remain useful for isolated unit fixtures. Expansion is
    bounded even for small graphs with exponential sharing.
    """
    require(type(schema) is dict, 'DEEPSEEK_SCHEMA_INVALID')
    graph = 'uid' in schema or 'refs' in schema
    refs = None
    if graph:
        require(set(schema) == {'uid', 'refs'} and type(schema['refs']) is dict
                and 0 < len(schema['refs']) <= 4096, 'DEEPSEEK_SCHEMA_INVALID')
        refs = schema['refs']
        for key, node in refs.items():
            require(type(key) is str and re.fullmatch(r'0|[1-9][0-9]{0,15}', key)
                    and int(key) <= SAFE_INT and type(node) is dict, 'DEEPSEEK_SCHEMA_INVALID')
    remaining = 8192
    remaining_bytes = json_config.MAX_BYTES
    node_costs = {}
    reachable = set()

    def walk(value, active, depth):
        nonlocal remaining, remaining_bytes
        remaining -= 1
        require(depth <= 64 and remaining >= 0, 'DEEPSEEK_SCHEMA_LIMIT')
        if refs is not None:
            require(type(value) is int and 0 <= value <= SAFE_INT
                    and str(value) in refs, 'DEEPSEEK_SCHEMA_REFERENCE_INVALID')
            require(value not in active, 'DEEPSEEK_SCHEMA_CYCLE')
            active = active | {value}
            reachable.add(str(value))
            value = refs[str(value)]
        require(type(value) is dict and 'uid' not in value and 'refs' not in value,
                'DEEPSEEK_SCHEMA_INVALID')
        require(type(value.get('meta', {})) is dict, 'DEEPSEEK_SCHEMA_INVALID')
        # Bound the expanded representation before later snapshot serialization.
        # Counting a wire node with its reference placeholders overestimates
        # its own contribution; each referenced child is charged separately.
        key = id(value)
        if key not in node_costs:
            node_costs[key] = len(encode(value))
        remaining_bytes -= node_costs[key]
        require(remaining_bytes >= 0, 'DEEPSEEK_SCHEMA_LIMIT')
        result = {k: v for k, v in value.items() if k not in ('dict', 'list', 'inner', 'sKey')}
        if 'dict' in value:
            require(type(value['dict']) is dict, 'DEEPSEEK_SCHEMA_INVALID')
            result['dict'] = {k: walk(v, active, depth + 1) for k, v in value['dict'].items()}
        if 'list' in value:
            require(type(value['list']) is list, 'DEEPSEEK_SCHEMA_INVALID')
            result['list'] = [walk(v, active, depth + 1) for v in value['list']]
        for field in ('inner', 'sKey'):
            if field in value:
                result[field] = walk(value[field], active, depth + 1)
        return result

    tree = walk(schema['uid'] if graph else schema, set(), 0)
    require(refs is None or reachable == set(refs), 'DEEPSEEK_SCHEMA_UNREACHABLE')
    return tree


def observation(identity, descriptor_raw, writable, registered):
    require(type(identity) is Identity, 'DEEPSEEK_IDENTITY_INVALID')
    identity.validate()
    require(writable is True, 'DEEPSEEK_NOT_WRITABLE')
    require(type(registered) is list and len(registered) <= 4096
            and all(type(n) is str and 0 < len(n) <= 512 for n in registered)
            and len(set(registered)) == len(registered), 'DEEPSEEK_REGISTRY_INVALID')
    require(KEY not in registered, 'DEEPSEEK_REGISTERED_NAME_CONFLICT')
    descriptor = read(descriptor_raw)
    require(set(descriptor) == {'ns', 'schema', 'revision', 'applies', 'value', 'base', 'user', 'secrets', 'autoGenerate'}
            and descriptor['ns'] == identity.entry and descriptor['applies'] == 'live'
            and type(descriptor['revision']) is int and 0 <= descriptor['revision'] <= SAFE_INT
            and type(descriptor['autoGenerate']) is bool and type(descriptor['schema']) is dict
            and type(descriptor['secrets']) is list, 'DEEPSEEK_NAMESPACE_INVALID')
    descriptor['schema'] = normalize_schema(descriptor['schema'])
    layers = []
    for name in ('value', 'base', 'user'):
        layer = descriptor[name]
        require(type(layer) is dict and ('servers' not in layer or type(layer['servers']) is dict),
                'DEEPSEEK_LAYER_INVALID')
        layers.append(layer.get('servers', {}))
    effective, base, user = layers
    require(KEY not in base, 'DEEPSEEK_INHERITED_NAME_CONFLICT')
    for secret in descriptor['secrets']:
        require(type(secret) is dict and set(secret) == {'path', 'set'} and type(secret['path']) is list
                and all(type(p) is str for p in secret['path']) and type(secret['set']) is bool,
                'DEEPSEEK_SECRET_DESCRIPTOR_INVALID')
        path = secret['path']
        require(path[:len(PATH)] != PATH and PATH[:len(path)] != path, 'DEEPSEEK_OWNED_SECRET_HIDDEN')
    # Reject root/servers/VF schema-declared secrets, including union branches.
    def schema_secrets(node, path):
        require(type(node) is dict, 'DEEPSEEK_SCHEMA_INVALID')
        meta = node.get('meta', {})
        require(type(meta) is dict, 'DEEPSEEK_SCHEMA_INVALID')
        require(meta.get('role') != 'secret', 'DEEPSEEK_OWNED_SECRET_HIDDEN')
        branches = node.get('list', [])
        children = node.get('dict', {})
        require(type(branches) is list and type(children) is dict, 'DEEPSEEK_SCHEMA_INVALID')
        for branch in branches:
            schema_secrets(branch, path)
        if 'sKey' in node:
            schema_secrets(node['sKey'], [])
        if not path:
            # Child fields (headers etc.) must not hide data in a whole-node set.
            for child in children.values():
                schema_secrets(child, [])
            if 'inner' in node:
                schema_secrets(node['inner'], [])
        else:
            child = children.get(path[0], node.get('inner'))
            require(child is not None, 'DEEPSEEK_SCHEMA_INVALID')
            schema_secrets(child, path[1:])
    schema_secrets(descriptor['schema'], PATH)
    protected = json.loads(encode(descriptor))
    del protected['revision']
    for name in ('value', 'user'):
        protected[name].get('servers', {}).pop(KEY, None)
        if protected[name].get('servers') == {}:
            del protected[name]['servers']
    protected['user'].pop('servers', None)
    return descriptor, effective, user, digest({'identity': asdict(identity), 'descriptor': descriptor,
        'registered': sorted(registered)}), digest({'descriptor': protected, 'registered': sorted(registered)})


@dataclass(frozen=True, repr=False)
class SettingsPlan:
    identity: Identity
    expected_revision: int
    before_digest: str
    protected_digest: str
    target: bytes | None
    other_user_before: bytes
    other_effective_before: bytes
    changed: bool
    restored_user_sha256: str | None = None
    empty_servers_cleanup: bool = False

    def mutation(self):
        """In-memory native call arguments. Caller must validate_submit first."""
        require(type(self.empty_servers_cleanup) is bool, 'DEEPSEEK_EMPTY_CONTAINER_PLAN_INVALID')
        ops = []
        if self.changed:
            ops = [{'op': 'unset', 'path': list(PATH)}] if self.target is None else [
                {'op': 'set', 'path': list(PATH), 'value': json.loads(self.target)}]
        if self.empty_servers_cleanup:
            require(self.changed and self.target is None and self.restored_user_sha256 == digest({})
                    and self.other_user_before == encode({}) and self.other_effective_before == encode({}),
                    'DEEPSEEK_EMPTY_CONTAINER_PLAN_INVALID')
            ops.append({'op': 'unset', 'path': ['servers']})
        return self.identity.entry, ops, self.expected_revision

    def receipt(self):
        return {'action': 'unchanged' if not self.changed else ('unset' if self.target is None else 'set'),
                'observation_sha256': self.before_digest, 'expected_revision': self.expected_revision,
                'mutation_executed': False, 'disk_written': False, 'desktop_acceptance': False,
                'mcp_acceptance': False, 'empty_servers_cleanup_planned': self.empty_servers_cleanup}


def plan_update(identity, descriptor_raw, *, writable, registered, desired, expected_owned_sha256):
    desc, effective, user, before, protected = observation(identity, descriptor_raw, writable, registered)
    node = user.get(KEY)
    require((digest(node) if KEY in user else None) == expected_owned_sha256, 'DEEPSEEK_OWNERSHIP_CONFLICT')
    if KEY in user:
        validate_desired(node)
        effective_matches(effective.get(KEY), node)
    else:
        require(KEY not in effective, 'DEEPSEEK_EFFECTIVE_NAME_CONFLICT')
    if desired is not None:
        validate_desired(desired)
    elif KEY in user:
        # ConfigEditor removes the whole override when next == inherited. Until
        # origin-aware layer recovery exists, do not risk removing user intent.
        others = {k: v for k, v in user.items() if k != KEY}
        inherited = desc['base'].get('servers', {})
        collapse = others and all(k in inherited and encode(normalize_server(v)) ==
                                  encode(normalize_server(inherited[k])) for k, v in others.items())
        require(not collapse, 'DEEPSEEK_NATIVE_UNSET_REQUIRES_LAYER_RESTORE')
    target = None if desired is None else encode(desired)
    changed = (KEY in user) if target is None else (KEY not in user or encode(node) != target)
    other_user = {k: v for k, v in user.items() if k != KEY}
    other_effective = {k: v for k, v in effective.items() if k != KEY}
    return SettingsPlan(identity, desc['revision'], before, protected, target,
                        encode(other_user), encode(other_effective), changed)


def validate_submit(plan, identity, descriptor_raw, *, writable, registered):
    _, _, _, before, _ = observation(identity, descriptor_raw, writable, registered)
    require(identity == plan.identity and before == plan.before_digest, 'DEEPSEEK_PRECONDITION_CHANGED')
    return plan.mutation()


def normalize_server(node):
    """Drop only defaults from the pinned native schema, for inherited copies."""
    require(type(node) is dict, 'DEEPSEEK_OTHER_SERVER_INVALID')
    value = json.loads(encode(node))
    reconnect = value.get('reconnect')
    if type(reconnect) is dict:
        for key, default in DEFAULTS['reconnect'].items():
            if key in reconnect and encode(reconnect[key]) == encode(default):
                del reconnect[key]
        if not reconnect:
            del value['reconnect']
    defaults = {**DEFAULTS, 'enabled': True, 'headers': {}, 'url': ''}
    for key, default in defaults.items():
        if key != 'reconnect' and key in value and encode(value[key]) == encode(default):
            del value[key]
    return value


def verify_user_layer(plan, user):
    before = json.loads(plan.other_user_before)
    effective = json.loads(plan.other_effective_before)
    after = {k: v for k, v in user.items() if k != KEY}
    for key, value in before.items():
        require(key in after and encode(after[key]) == encode(value), 'DEEPSEEK_USER_LAYER_CHANGED')
    for key in set(after) - set(before):
        require(key in effective and encode(normalize_server(after[key])) == encode(normalize_server(effective[key])),
                'DEEPSEEK_UNEXPECTED_MATERIALIZATION')


def verify_readback(plan, identity, descriptor_raw, *, writable, registered):
    desc, effective, user, _, protected = observation(identity, descriptor_raw, writable, registered)
    require(identity == plan.identity and protected == plan.protected_digest, 'DEEPSEEK_READBACK_CONTEXT_CHANGED')
    if plan.restored_user_sha256 is None:
        verify_user_layer(plan, user)
    else:
        # Only an origin-bound removal may intentionally drop materialization
        # created by this tool. The exact original root layer must come back.
        require(plan.target is None and digest(desc['user']) == plan.restored_user_sha256,
                'DEEPSEEK_ORIGINAL_USER_LAYER_NOT_RESTORED')
    require(desc['revision'] > plan.expected_revision if plan.changed else desc['revision'] == plan.expected_revision,
            'DEEPSEEK_READBACK_REVISION')
    if plan.target is None:
        require(KEY not in user and KEY not in effective, 'DEEPSEEK_REMOVAL_NOT_EFFECTIVE')
        owned = None
    else:
        require(KEY in user and encode(user[KEY]) == plan.target, 'DEEPSEEK_READBACK_VALUE')
        effective_matches(effective.get(KEY), user[KEY])
        owned = digest(user[KEY])
    return {**plan.receipt(), 'configuration_readback_verified': True, 'owned_sha256': owned}
