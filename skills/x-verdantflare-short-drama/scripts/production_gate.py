#!/usr/bin/env python3
"""Local production handoff. Validates records, not perception or authorization."""
import argparse
import copy
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile

import validate_reference_reuse as refs
import validate_visual_review as visual

VERSION = '1'


class GateError(ValueError):
    pass


def require(ok, code):
    if not ok:
        raise GateError(code)


def strict_load(path):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, 'duplicate_json_key')
            result[key] = value
        return result
    def invalid(value):
        raise GateError('nonfinite_json_number')
    value = json.loads(Path(path).read_text(encoding='utf-8'),
                       object_pairs_hook=pairs, parse_constant=invalid)
    refs.request_digest(value)  # Also rejects overflow such as 1e999.
    return value


def verified(spec, base):
    require(isinstance(spec, dict) and isinstance(spec.get('path'), str), 'file_spec_missing')
    require(not Path(spec['path']).is_absolute(), 'relative_path_required')
    path = base / spec['path']
    require(path.is_file(), 'evidence_file_missing')
    require(refs.digest(path) == spec.get('sha256'), 'file_digest_mismatch')
    return path


def verify_tree(value, base):
    if isinstance(value, dict):
        if 'path' in value and 'sha256' in value:
            verified(value, base)
        for child in value.values():
            verify_tree(child, base)
    elif isinstance(value, list):
        for child in value:
            verify_tree(child, base)


def code_digest():
    return refs.request_digest({p.name: refs.digest(p) for p in
                               (Path(__file__), Path(refs.__file__), Path(visual.__file__))})


def load_inputs(packet_path, basis_path, basis_sha256):
    packet_path, basis_path = Path(packet_path), Path(basis_path)
    require(refs.digest(basis_path) == basis_sha256, 'upstream_basis_changed')
    basis, packet = strict_load(basis_path), strict_load(packet_path)
    require(packet.get('basis_sha256') == basis_sha256, 'packet_basis_mismatch')
    base = packet_path.parent
    unit = basis['units'][packet['unit']]
    data = {}
    for key in ('request', 'imports', 'audit', 'locks', 'action', 'camera', 'semantics'):
        p = verified(packet[key], base)
        data[key] = strict_load(p)
        verify_tree(data[key], p.parent)
    require(all((base / packet[k]['path']).parent.resolve() == base.resolve()
                for k in data), 'packet_documents_must_share_directory')
    if 'soundtrack' in packet:
        verified(packet['soundtrack'], base)
    visual_hash = refs.request_digest({'code': code_digest(), 'basis': basis_sha256,
        'unit': packet['unit'], 'inputs': {k: packet[k]['sha256'] for k in data if k != 'semantics'}})
    fingerprints = {'visual': visual_hash}
    if 'soundtrack' in packet:
        fingerprints['sound'] = refs.request_digest({'visual': visual_hash, 'soundtrack': packet['soundtrack']})
    return packet, basis, unit, data, fingerprints


def check_events(action, unit):
    state = copy.deepcopy(action['initial'])
    seen = set()
    events = action['events']
    require(bool(events), 'action_events_missing')
    for event in events:
        key = event['id']
        require(key not in seen, 'duplicate_event')
        require(set(event.get('after', [])) <= seen, 'event_order_conflict:' + key)
        require(all(state.get(k) == v for k, v in event['requires'].items()),
                'event_precondition_conflict:' + key)
        require(bool(event.get('description')), 'event_description_missing')
        state.update(event['sets'])
        seen.add(key)
    require(set(unit['required_events']) <= seen, 'required_event_missing')
    require(all(state.get(k) == v for k, v in unit.get('final_state', {}).items()), 'final_state_conflict')
    for before, after in unit.get('event_order', []):
        order = [e['id'] for e in events]
        require(order.index(before) < order.index(after), 'required_event_order_conflict')


def check_camera(camera, unit, base):
    expected = unit['camera']
    require(camera['blend_sha256'] == expected['blend_sha256'], 'camera_engineering_version')
    require(camera['parameters'] == expected['parameters'], 'camera_parameters_changed')
    require(camera['purpose'] == 'space_and_camera', 'camera_purpose_invalid')
    checkpoints = camera['checkpoints']
    require(set(expected['checkpoints']) <= set(checkpoints), 'camera_checkpoint_missing')
    for key in expected['checkpoints']:
        item = checkpoints[key]
        require(item['outcome'] == 'pass' and bool(item['observation']), 'camera_observation_missing:' + key)
        require(bool(item['evidence']), 'camera_evidence_missing')
        for evidence in item['evidence']:
            verified(evidence, base)
    if expected.get('moving'):
        require(camera.get('path_reviewed') is True, 'camera_path_unchecked')
        verified(camera['preview'], base)


def check(packet_path, basis_path, basis_sha256):
    packet, basis, unit, d, fingerprints = load_inputs(packet_path, basis_path, basis_sha256)
    base = Path(packet_path).parent
    request = d['request'].get('arguments', d['request'])
    require(request['project_id'] == basis['project_id'], 'project_mismatch')
    for key, value in unit['model'].items():
        require(request.get(key) == value, 'model_constraint:' + key)
    require(bool(request.get('idempotency_key')), 'idempotency_key_missing')
    # Upstream requirements cannot be weakened by editing packet-local locks.
    locks = copy.deepcopy(d['locks'])
    locks['required_references'] = unit['required_references']
    errors, target = refs.validate_request(d['imports'], d['request'], locks, require_bindings=True)
    require(not errors, 'request_reference_check:' + '|'.join(errors))
    audit = refs.validate(d['imports'], d['audit'], base, target)
    require(not audit['errors'], 'reference_provenance_check:' + '|'.join(audit['errors']))
    provenance = {r['key']: r for r in d['audit']['references']}
    for key, kind in unit.get('provenance', {}).items():
        item = provenance[key]
        require(item.get('source_kind') == kind, 'reference_provenance_relabel:' + key)
        if kind == 'blender_actor_proxy':
            require(item.get('role') in {'space', 'camera'}, 'proxy_not_performance:' + key)
    check_events(d['action'], unit)
    check_camera(d['camera'], unit, base)
    required = dict(unit['semantic_checks'])
    if unit.get('repeat_defect'):
        core = {k: v for k, v in request.items() if k not in {'seed', 'idempotency_key', 'title'}}
        require(refs.request_digest(core) != unit.get('previous_core_sha256'), 'seed_only_retry')
        required['method_change'] = 'visual'
    for key, domain in required.items():
        item = d['semantics']['checks'].get(key, {})
        require(item.get('fingerprint') == fingerprints.get(domain) and domain in fingerprints,
                'semantic_stale:' + key)
        require(item.get('outcome') == 'pass', 'semantic_not_passed:' + key)
        require(item.get('reviewer') and item.get('observation') and item.get('evidence'),
                'semantic_observation_missing:' + key)
        for evidence in item['evidence']:
            verified(evidence, base)
    return {'record_check_passed': True, 'visual_quality_verified': False,
            'request_sha256': refs.request_digest(d['request']), 'fingerprints': fingerprints,
            'request': d['request'], 'warnings': audit['warnings']}


def atomic_write(path, value):
    path = Path(path)
    fd, temp = tempfile.mkstemp(prefix='.' + path.name, dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(value, stream, ensure_ascii=False, sort_keys=True, allow_nan=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def submit(packet, basis, basis_sha256, journal, adapter):
    """Adapter.preflight/create take the exact request. Existing journals only recover."""
    journal = Path(journal)
    if journal.exists():
        old = strict_load(journal)
        return adapter.recover(old)  # Do not block recovery on obsolete input checks.
    checked = check(packet, basis, basis_sha256)
    frozen = json.loads(json.dumps(checked['request'], allow_nan=False))
    frozen_hash = refs.request_digest(frozen)
    adapter.preflight(copy.deepcopy(frozen))
    again = check(packet, basis, basis_sha256)
    require(again['fingerprints'] == checked['fingerprints'] and
            again['request_sha256'] == frozen_hash, 'input_changed_during_preflight')
    record = {'state': 'submission_unknown', 'request': frozen, 'request_sha256': frozen_hash,
              'basis_sha256': basis_sha256, 'fingerprints': checked['fingerprints']}
    # Persist before any side effect; O_EXCL arbitrates concurrent submitters.
    try:
        fd = os.open(journal, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        return adapter.recover(strict_load(journal))
    with os.fdopen(fd, 'w', encoding='utf-8') as stream:
        json.dump(record, stream, ensure_ascii=False, allow_nan=False)
        stream.flush()
        os.fsync(stream.fileno())
    response = adapter.create(copy.deepcopy(frozen))  # exception leaves unknown; never auto retry.
    record.update({'state': 'submitted', 'response': response})
    task_id = response.get('video_task_id', response.get('task_id'))
    if task_id:
        record['task_id'] = task_id
    else:
        record['state'] = 'submission_unknown'
    atomic_write(journal, record)
    return record


class MCPAdapter:
    """Load the installed Video client explicitly; no copy of its service API."""
    def __init__(self, video_client_path):
        path = Path(video_client_path).resolve()
        sys.path.insert(0, str(path.parent))
        try:
            spec = importlib.util.spec_from_file_location('_production_video_client', path)
            self.client = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = self.client
            spec.loader.exec_module(self.client)
        finally:
            sys.path.pop(0)
        self.config = self.client.load_mcp_config()
        self.inventory = None
        self.admitted_digest = None

    def preflight(self, saved):
        self.admitted_digest = None
        c = self.client
        self.inventory = c.check_mcp_tools(self.config)
        args = saved.get('arguments', saved)
        tool = saved.get('tool', self.inventory['create_tool'])
        require(tool == self.inventory['create_tool'], 'create_tool_changed')
        _, _, selected = c.select_route(self.inventory['capabilities'], args.get('route'), args.get('model'))
        c.validate_capability(args, selected)
        c.validate_tool(self.inventory, tool, args)
        require('video.preflight' in self.inventory['tools'], 'registered_preflight_missing')
        # Existing Video protocol excludes idempotency_key from preflight only.
        preflight = {k: v for k, v in args.items() if k != 'idempotency_key'}
        c.validate_tool(self.inventory, 'video.preflight', preflight)
        quote = c.call_mcp_tool(self.config, 'video.preflight', preflight)
        require(quote.get('can_submit') is True, 'service_preflight_not_ready')
        self.admitted_digest = refs.request_digest(saved)

    def create(self, saved):
        require(self.inventory is not None and
                getattr(self, 'admitted_digest', None) == refs.request_digest(saved), 'preflight_required')
        self.admitted_digest = None
        return self.client.call_mcp_tool(self.config, self.inventory['create_tool'], saved.get('arguments', saved))

    def recover(self, record):
        if record.get('task_id'):
            return {'recovery': self.client.mcp_status(self.config, record['task_id']), 'journal': record}
        return {'state': 'reconciliation_required', 'request_sha256': record.get('request_sha256')}


def adopt(report_path, selection_path, expected_selection_sha256):
    """Atomically replace local edit selection, not an encoded film or approval."""
    report_path, selection_path = Path(report_path), Path(selection_path)
    base, report = report_path.parent, strict_load(report_path)
    verify_tree(report, base)
    basis_path = verified(report['basis'], base)
    unit = strict_load(basis_path)['units'][report['unit']]
    review_requirements = unit['review_requirements']
    v = copy.deepcopy(report['visual_review'])
    for key in ('contact_shot', 'join_required'):
        require(v.get(key) == review_requirements[key], 'output_scope_weakened:' + key)
    require(set(v['actors']) == set(review_requirements['actors']), 'output_actor_scope_changed')
    for actor, expected in review_requirements['actors'].items():
        for key in ('original_identity', 'approved_look'):
            require(v['actors'][actor][key]['sha256'] == expected[key], 'output_actor_baseline_changed')
    outcome = visual.validate(v, base)
    require(outcome['evidence_complete'], 'output_review_incomplete:' + '|'.join(outcome['errors']))
    require(v['media_kind'] == 'video', 'video_required')
    current = strict_load(verified(v['current_review'], base))
    # A candidate can be reviewed locally, but cannot become a selected take silently.
    require(current.get('status') == 'approved_video', 'video_selection_not_approved')
    request_hash = refs.digest(verified(v['request'], base))
    require(report['request_sha256'] == request_hash, 'output_request_mismatch')
    origin = strict_load(verified(report['origin_journal'], base))
    require(origin.get('state') == 'submitted' and origin.get('task_id'), 'origin_task_unconfirmed')
    require(origin['request_sha256'] == refs.request_digest(strict_load(verified(v['request'], base))),
            'origin_request_mismatch')
    require(current.get('task_id') == origin['task_id'], 'candidate_task_mismatch')
    for kind in ('video', 'audio'):
        review = report['playback'][kind]
        require(review['kind'] == 'observed_' + kind and review['coverage'] == 'complete_original_speed',
                'playback_modality_mismatch:' + kind)
        require(review['candidate_sha256'] == v['candidate']['sha256'] and
                review['outcome'] == 'pass' and review['observation'] and review['reviewer'],
                'playback_observation_missing:' + kind)
        verified(review['evidence'], base)
    observed = report['observed_events']
    require(set(unit['required_events']) <= set(observed), 'observed_event_missing')
    for before, after in unit.get('event_order', []):
        require(observed[before]['frame'] < observed[after]['frame'], 'observed_event_order_conflict')
    for key in unit['required_events']:
        event = observed[key]
        require(type(event['frame']) is int and event['frame'] >= 0 and event['observation'], 'event_observation_missing')
        verified(event['evidence'], base)
    lock_path = selection_path.with_suffix(selection_path.suffix + '.lock')
    try:
        lock = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise GateError('selection_locked') from exc
    try:
        os.close(lock)
        require(refs.digest(selection_path) == expected_selection_sha256, 'selection_changed')
        old = strict_load(selection_path)
        require(old.get('basis_sha256') == report['basis']['sha256'] == origin['basis_sha256'],
                'selection_basis_mismatch')
        new = copy.deepcopy(old)
        new.setdefault('takes', {})[report['unit']] = {
            'candidate': {**v['candidate'], 'path': os.path.relpath(verified(v['candidate'], base), selection_path.parent)},
            'origin_report': os.path.relpath(report_path, selection_path.parent),
            'report_sha256': refs.digest(report_path), 'status': 'approved_video'}
        backup = selection_path.with_name(selection_path.name + '.' + expected_selection_sha256 + '.bak')
        if not backup.exists():
            with backup.open('xb') as stream:
                stream.write(selection_path.read_bytes())
        require(refs.digest(backup) == expected_selection_sha256, 'selection_backup_mismatch')
        require(refs.digest(selection_path) == expected_selection_sha256, 'selection_changed')
        atomic_write(selection_path, new)
        return {'selection_updated': True, 'film_rendered': False, 'approval_granted': False}
    finally:
        lock_path.unlink()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['check'])
    parser.add_argument('--packet', type=Path, required=True)
    parser.add_argument('--basis', type=Path, required=True)
    parser.add_argument('--basis-sha256', required=True)
    args = parser.parse_args()
    try:
        result = check(args.packet, args.basis, args.basis_sha256)
        result.pop('request')  # Do not dump prompts or local paths to logs.
    except (KeyError, TypeError, ValueError, OSError) as exc:
        result = {'record_check_passed': False, 'error': str(exc) if isinstance(exc, GateError) else type(exc).__name__}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result['record_check_passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
