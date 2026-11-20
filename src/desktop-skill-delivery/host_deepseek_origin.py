"""Origin-bound pure planning; not a persistent ledger or management client.

Only verified before/after chains create or advance records. Records contain
hashes, never copies of other connections or their secrets. The caller must
authenticate observations and protect records; hashes do not prove provenance.
The pinned native editor may collapse synthetic materialization when the
original user layer was empty. Existing explicit layers remain protected.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import json
import re

import host_deepseek as h


def _non_vf_user(desc):
    result = json.loads(h.encode(desc['user']))
    result.get('servers', {}).pop(h.KEY, None)
    return h.digest(result)


@dataclass(frozen=True, repr=False)
class LayerOrigin:
    identity: h.Identity
    protected_digest: str
    original_user_sha256: str
    original_user_empty: bool
    current_non_vf_user_sha256: str
    owned_sha256: str

    def validate(self):
        h.require(type(self.identity) is h.Identity, 'DEEPSEEK_ORIGIN_INVALID')
        self.identity.validate()
        for value in (self.protected_digest, self.original_user_sha256,
                      self.current_non_vf_user_sha256, self.owned_sha256):
            h.require(type(value) is str and re.fullmatch('[0-9a-f]{64}', value),
                      'DEEPSEEK_ORIGIN_INVALID')
        h.require(type(self.original_user_empty) is bool and
                  self.original_user_empty == (self.original_user_sha256 == h.digest({})),
                  'DEEPSEEK_ORIGIN_INVALID')

    def receipt(self):
        self.validate()
        return {'original_user_sha256': self.original_user_sha256,
                'original_user_empty': self.original_user_empty,
                'current_non_vf_user_sha256': self.current_non_vf_user_sha256,
                'owned_sha256': self.owned_sha256,
                'persistent_ledger_written': False, 'desktop_acceptance': False,
                'mcp_acceptance': False}


def plan_first_install(identity, descriptor_raw, *, writable, registered, desired):
    """Reject known irreversible shapes before first addition, not at uninstall."""
    plan = h.plan_update(identity, descriptor_raw, writable=writable, registered=registered,
                         desired=desired, expected_owned_sha256=None)
    h.require(plan.target is not None and plan.changed, 'DEEPSEEK_ORIGIN_FIRST_INSTALL_REQUIRED')
    desc, effective, user, _, _ = h.observation(identity, descriptor_raw, writable, registered)
    inherited = desc['base'].get('servers', {})
    h.require(not (desc['user'] == {'servers': {}} and not inherited),
              'DEEPSEEK_EXPLICIT_EMPTY_LAYER_RESTORE_UNVERIFIED')
    if desc['user'] == {} and not effective:
        h.require(not inherited, 'DEEPSEEK_EMPTY_ROOT_RESTORE_UNVERIFIED')
    collapse = user and all(k in inherited and h.encode(h.normalize_server(v)) ==
                           h.encode(h.normalize_server(inherited[k])) for k, v in user.items())
    h.require(not collapse, 'DEEPSEEK_NATIVE_UNSET_REQUIRES_LAYER_RESTORE')
    return plan


def bind_origin(install_plan, identity, before_raw, after_raw, *, writable, registered):
    """Bind the first addition, never adopt an existing or merely matching VF."""
    h.require(type(install_plan) is h.SettingsPlan and install_plan.target is not None,
              'DEEPSEEK_ORIGIN_FIRST_INSTALL_REQUIRED')
    expected = h.plan_update(identity, before_raw, writable=writable, registered=registered,
                            desired=json.loads(install_plan.target), expected_owned_sha256=None)
    h.require(expected == install_plan and expected.changed,
              'DEEPSEEK_ORIGIN_FIRST_INSTALL_REQUIRED')
    h.validate_submit(install_plan, identity, before_raw, writable=writable, registered=registered)
    receipt = h.verify_readback(install_plan, identity, after_raw, writable=writable, registered=registered)
    before = h.read(before_raw)
    after = h.read(after_raw)
    origin = LayerOrigin(identity, install_plan.protected_digest, h.digest(before['user']),
                         before['user'] == {}, _non_vf_user(after), receipt['owned_sha256'])
    origin.validate()
    return origin


def plan_owned(origin, identity, descriptor_raw, *, writable, registered, desired):
    """Plan using one trusted origin, keeping the legacy no-origin guard intact."""
    h.require(type(origin) is LayerOrigin, 'DEEPSEEK_ORIGIN_REQUIRED')
    origin.validate()
    desc, effective, user, _, protected = h.observation(identity, descriptor_raw, writable, registered)
    h.require(identity == origin.identity and protected == origin.protected_digest,
              'DEEPSEEK_ORIGIN_CONTEXT_CHANGED')
    h.require(_non_vf_user(desc) == origin.current_non_vf_user_sha256,
              'DEEPSEEK_ORIGIN_USER_CHANGED')
    if desired is None and origin.original_user_sha256 == h.digest({'servers': {}}):
        h.require(bool(desc['base'].get('servers', {})),
                  'DEEPSEEK_EXPLICIT_EMPTY_LAYER_RESTORE_UNVERIFIED')
    cleanup = False
    if desired is None and origin.original_user_empty and not (set(user) - {h.KEY}):
        # Remove only a container created by our first installation. The
        # native unset restores inherited values, so hidden base connections
        # must also be absent before the parent-path operation is permitted.
        h.require(not (set(effective) - {h.KEY}) and not desc['base'].get('servers', {}),
                  'DEEPSEEK_EMPTY_ROOT_RESTORE_UNVERIFIED')
        cleanup = True
    try:
        plan = h.plan_update(identity, descriptor_raw, writable=writable, registered=registered,
                             desired=desired, expected_owned_sha256=origin.owned_sha256)
    except h.Conflict as error:
        if str(error) != 'DEEPSEEK_NATIVE_UNSET_REQUIRES_LAYER_RESTORE':
            raise
        h.require(desired is None and origin.original_user_empty,
                  'DEEPSEEK_NATIVE_UNSET_REQUIRES_LAYER_RESTORE')
        # Re-run every ownership/effectiveness gate through a native no-op plan.
        # Change only the owned path; no entire-layer replace or synthetic key.
        plan = h.plan_update(identity, descriptor_raw, writable=writable, registered=registered,
                             desired=desc['user']['servers'][h.KEY],
                             expected_owned_sha256=origin.owned_sha256)
        plan = replace(plan, target=None, changed=True)
    if desired is None:
        plan = replace(plan, restored_user_sha256=origin.original_user_sha256,
                       empty_servers_cleanup=cleanup)
    return plan


def verify_and_advance(origin, plan, identity, before_raw, after_raw, *, writable, registered):
    """Return a new record, or retire it only after exact removal readback."""
    h.require(type(plan) is h.SettingsPlan, 'DEEPSEEK_ORIGIN_PLAN_MISMATCH')
    desired = None if plan.target is None else json.loads(plan.target)
    expected = plan_owned(origin, identity, before_raw, writable=writable,
                          registered=registered, desired=desired)
    h.require(plan == expected, 'DEEPSEEK_ORIGIN_PLAN_MISMATCH')
    h.validate_submit(plan, identity, before_raw, writable=writable, registered=registered)
    receipt = h.verify_readback(plan, identity, after_raw, writable=writable, registered=registered)
    if desired is None:
        return None, {**receipt, 'original_user_layer_restored': True,
                      'origin_record_retained': False, 'persistent_ledger_written': False}
    after = h.read(after_raw)
    updated = replace(origin, current_non_vf_user_sha256=_non_vf_user(after),
                      owned_sha256=receipt['owned_sha256'])
    updated.validate()
    return updated, {**receipt, 'original_user_layer_restored': False,
                     'origin_record_retained': True, 'persistent_ledger_written': False}
