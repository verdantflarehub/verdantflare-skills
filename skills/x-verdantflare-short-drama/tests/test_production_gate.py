"""Fault injection exercises submission effects, not just validation booleans."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import production_gate as gate


class Adapter:
    def __init__(self):
        self.sent = []
        self.recovered = []
        self.on_preflight = lambda: None
        self.fail_create = False

    def preflight(self, request):
        self.on_preflight()

    def create(self, request):
        self.sent.append(request)
        if self.fail_create:
            raise TimeoutError('mock lost acknowledgement')
        return {'video_task_id': 'mock-one'}

    def recover(self, record):
        self.recovered.append(record)
        return {'recovered': record['state']}


class GateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.adapter = Adapter()
        self.evidence = self.write('evidence.txt', b'synthetic test evidence; not actual viewing')
        approved = self.write('approved.json', {'decision': 'approved'})
        self.imports, self.audit = [], {'references': []}
        for key in ('face', 'look_back', 'camera'):
            image = self.write(key + '.bin', key.encode())
            self.imports.append({'key': key, 'artifact_id': 'art_' + key, 'sha256': image['sha256']})
            self.audit['references'].append({'key': key, 'role': 'camera' if key == 'camera' else key,
                'source_kind': 'blender_camera' if key == 'camera' else 'approved_image',
                'input': image, 'source': image, 'source_review': approved})
        self.request = {'project_id': 'fixture-project', 'idempotency_key': 'fixture-1',
            'route': 'h3-fasth3', 'model': 'minimax-h3-ref2va', 'quality_profile': 'fasth3-nvfp4',
            'prompt': 'Synthetic actor contacts, pushes, then releases. Not a real media request.',
            'references': {'images': [{'artifact_id': x['artifact_id'], 'purpose': x['key']} for x in self.imports]}}
        required = [{**x, 'basis': 'fixture-baseline'} for x in self.imports]
        self.unit = {'required_references': required,
            'model': {k: self.request[k] for k in ('route', 'model', 'quality_profile')},
            'provenance': {'camera': 'blender_camera'},
            'required_events': ['contact', 'push', 'release'],
            'event_order': [['contact', 'push'], ['push', 'release']],
            'final_state': {'contact': False},
            'camera': {'blend_sha256': 'a' * 64, 'parameters': {'lens': 35},
                'checkpoints': ['start', 'contact', 'end'], 'moving': True},
            'semantic_checks': {'listener': 'visual', 'action': 'visual', 'identity': 'visual'}}
        self.basis = {'project_id': 'fixture-project', 'units': {'u1': self.unit}}
        self.unit['review_requirements'] = {'contact_shot': False, 'join_required': False,
            'actors': {'A': {'original_identity': self.imports[0]['sha256'], 'approved_look': self.imports[1]['sha256']}}}
        self.camera = {'blend_sha256': 'a' * 64, 'parameters': {'lens': 35}, 'purpose': 'space_and_camera',
            'path_reviewed': True, 'preview': self.evidence,
            'checkpoints': {k: {'outcome': 'pass', 'observation': 'Synthetic unobstructed projection.',
                'evidence': [self.evidence]} for k in ('start', 'contact', 'end')}}
        self.action = {'initial': {'contact': False, 'hand': 'free'}, 'events': [
            {'id': 'contact', 'requires': {'hand': 'free'}, 'sets': {'contact': True}, 'description': 'Palm contacts sleeve.'},
            {'id': 'push', 'after': ['contact'], 'requires': {'contact': True}, 'sets': {'pushed': True}, 'description': 'Short impulse.'},
            {'id': 'release', 'after': ['push'], 'requires': {'pushed': True}, 'sets': {'contact': False}, 'description': 'Hands release.'}]}
        self.locks = {'project_id': 'fixture-project', 'required_references': required}
        self.packet = {'unit': 'u1'}
        self.freeze()

    def write(self, name, value):
        path = self.base / name
        path.write_bytes(value if isinstance(value, bytes) else json.dumps(value, ensure_ascii=False).encode())
        return {'path': name, 'sha256': gate.refs.digest(path)}

    def freeze(self, semantics=True):
        self.basis_spec = self.write('basis.json', self.basis)
        self.basis_sha = self.basis_spec['sha256']
        self.packet['basis_sha256'] = self.basis_sha
        self.locks['request_sha256'] = gate.refs.request_digest(self.request)
        by_id = {x['artifact_id']: x for x in self.imports}
        slots, _ = gate.refs.input_slots(self.request)
        self.locks['input_bindings'] = [{**x, 'key': by_id[x['artifact_id']]['key'],
            'sha256': by_id[x['artifact_id']]['sha256']} for x in slots]
        for key in ('request', 'imports', 'audit', 'locks', 'action', 'camera'):
            self.packet[key] = self.write(key + '.json', getattr(self, key))
        if 'semantics' not in self.packet:
            self.packet['semantics'] = self.write('semantics.json', {'checks': {}})
        self.write('packet.json', self.packet)
        fingerprints = gate.load_inputs(self.base/'packet.json', self.base/'basis.json', self.basis_sha)[4]
        if semantics:
            self.semantics = {'checks': {k: {'fingerprint': fingerprints[d], 'outcome': 'pass',
                'observation': 'Synthetic fixture judgment; no real perception claimed.',
                'reviewer': 'test-fixture', 'evidence': [self.evidence]}
                for k, d in self.unit['semantic_checks'].items()}}
            self.packet['semantics'] = self.write('semantics.json', self.semantics)
            self.write('packet.json', self.packet)
        return fingerprints

    def submit(self):
        return gate.submit(self.base/'packet.json', self.base/'basis.json', self.basis_sha,
                           self.base/'journal.json', self.adapter)

    def rejects(self, code):
        with self.assertRaisesRegex(gate.GateError, code):
            self.submit()
        self.assertEqual(self.adapter.sent, [])
        self.assertFalse((self.base/'journal.json').exists())

    def test_positive_exact_request_submitted_once_and_recovered(self):
        self.submit()
        self.submit()
        self.assertEqual(self.adapter.sent, [self.request])
        self.assertEqual(len(self.adapter.recovered), 1)

    def test_negative_01_remove_back_and_local_requirement(self):
        self.imports = [x for x in self.imports if x['key'] != 'look_back']
        self.audit['references'] = [x for x in self.audit['references'] if x['key'] != 'look_back']
        self.request['references']['images'].pop(1)
        self.locks['required_references'] = [x for x in self.locks['required_references'] if x['key'] != 'look_back']
        self.freeze()
        self.rejects('fixed reference missing')

    def test_negative_02_modify_prompt_model_or_image_after_check(self):
        for mode in ('prompt', 'model', 'pixels'):
            with self.subTest(mode=mode):
                original = (self.base/'request.json').read_bytes()
                image = (self.base/'face.bin').read_bytes()
                def mutate():
                    if mode == 'pixels':
                        (self.base/'face.bin').write_bytes(b'changed')
                    else:
                        request = copy.deepcopy(self.request)
                        request[mode] = 'changed'
                        self.write('request.json', request)
                self.adapter.on_preflight = mutate
                self.rejects('digest_mismatch|reference_provenance_check')
                (self.base/'request.json').write_bytes(original)
                (self.base/'face.bin').write_bytes(image)

    def test_negative_02_refrozen_request_does_not_reuse_old_semantics(self):
        self.request['prompt'] += ' changed movement'
        self.freeze(semantics=False)
        self.rejects('semantic_stale')

    def test_negative_03_proxy_relabel_and_positive_camera_only(self):
        self.submit()  # Pure camera preview is valid.
        (self.base/'journal.json').unlink()
        self.adapter.sent.clear()
        self.unit['provenance']['camera'] = 'blender_actor_proxy'
        self.audit['references'][2]['source_kind'] = 'blender_camera'
        self.freeze()
        self.rejects('provenance_relabel')
        self.audit['references'][2].update(source_kind='blender_actor_proxy', role='performance')
        self.freeze()
        self.rejects('proxy_not_performance')

    def test_negative_04_generic_listener_semantic_failure(self):
        # A content reviewer rejects the text; this tests enforcement, not NLP.
        self.semantics['checks']['listener'].update(outcome='fail', observation='Only says shocked; no listener action after line.')
        self.packet['semantics'] = self.write('semantics.json', self.semantics)
        self.write('packet.json', self.packet)
        self.rejects('semantic_not_passed:listener')

    def test_negative_07_same_defect_needs_method_change(self):
        self.unit['repeat_defect'] = 'stiff contact'
        self.request['seed'] = 2
        self.freeze()
        self.rejects('semantic_stale:method_change')

    def test_negative_08_unknown_submission_never_creates_again(self):
        self.adapter.fail_create = True
        with self.assertRaises(TimeoutError):
            self.submit()
        (self.base/'face.bin').unlink()  # Broken preparation must not block old-task recovery.
        self.submit()
        self.assertEqual(len(self.adapter.sent), 1)
        self.assertEqual(self.adapter.recovered[0]['state'], 'submission_unknown')

    def test_negative_09_camera_missing_contact_or_changed_lens(self):
        self.camera['checkpoints'].pop('contact')
        self.freeze()
        self.rejects('camera_checkpoint_missing')
        self.camera['checkpoints']['contact'] = self.camera['checkpoints']['start']
        self.camera['parameters']['lens'] = 50
        self.freeze()
        self.rejects('camera_parameters_changed')

    def action_fixture(self, initial, events, order):
        self.action = {'initial': initial, 'events': events}
        self.unit['required_events'] = [x['id'] for x in events]
        self.unit['event_order'] = order
        self.unit['final_state'] = {}

    def test_negative_10_rope_break_without_cut(self):
        self.unit['required_events'] += ['blade_contact', 'draw_cut', 'break']
        self.freeze()
        self.rejects('required_event_missing')

    def test_negative_11_takeoff_water_swim_and_hit(self):
        cases = [
            ('jump', {'support': 'deck'}, {'support': 'air'}),
            ('splash', {'water_contact': False}, {'water_contact': True}),
            ('swim', {'left_hand': 'seal'}, {'left_hand': 'free'}),
            ('hit_reaction', {'hit': False}, {'hit': True})]
        for key, initial, needs in cases:
            with self.subTest(action=key):
                self.action_fixture(initial, [{'id': key, 'requires': needs, 'sets': {}, 'description': key}], [])
                self.freeze()
                self.rejects('event_precondition_conflict')

    def test_positive_soundtrack_only_keeps_visual_fingerprint(self):
        visual = self.freeze()['visual']
        self.packet['soundtrack'] = self.write('sound.bin', b'new soundtrack')
        self.unit['semantic_checks']['sound'] = 'sound'
        # Adding a new requirement changes basis, so establish it before comparison.
        old = self.freeze()
        identity_review = copy.deepcopy(self.semantics['checks']['identity'])
        self.packet['soundtrack'] = self.write('sound.bin', b'different soundtrack')
        new = self.freeze()
        self.assertEqual(old['visual'], new['visual'])
        self.assertNotEqual(old['sound'], new['sound'])
        self.assertEqual(identity_review, self.semantics['checks']['identity'])
        self.submit()

    def output_review(self):
        candidate = self.write('candidate.bin', b'synthetic video')
        request = self.packet['request']
        current = self.write('current.json', {'status': 'approved_video', 'task_id': 'mock-one', 'candidate_sha256': candidate['sha256'],
                                             'request_sha256': request['sha256']})
        actor = {'original_identity': self.audit['references'][0]['source'],
                 'approved_look': self.audit['references'][1]['source']}
        checks = {}
        for key in ('space', 'props', 'action', 'A/identity', 'A/wardrobe'):
            checks[key] = {'outcome': 'pass', 'observation': 'Synthetic test observation',
                           'evidence': [self.evidence], 'source_frames': [0, 1, 2]}
        checks['A/identity']['comparison'] = {**actor, 'candidate': candidate}
        v = {'candidate': candidate, 'request': request, 'current_review': current, 'actors': {'A': actor},
             'contact_shot': False, 'join_required': False, 'media_kind': 'video', 'playback_reviewed': True, 'checks': checks}
        origin = self.write('origin.json', {'state': 'submitted', 'task_id': 'mock-one', 'basis_sha256': self.basis_sha,
                                           'request_sha256': gate.refs.request_digest(self.request)})
        report = {'visual_review': v, 'request_sha256': request['sha256'], 'basis': self.basis_spec, 'origin_journal': origin,
            'unit': 'u1', 'playback': {kind: {'kind': 'observed_' + kind, 'coverage': 'complete_original_speed',
                'candidate_sha256': candidate['sha256'], 'outcome': 'pass', 'reviewer': 'test-fixture',
                'observation': 'Synthetic fixture only', 'evidence': self.evidence} for kind in ('audio', 'video')},
            'observed_events': {key: {'frame': n, 'observation': key, 'evidence': self.evidence}
                                for n, key in enumerate(self.unit['required_events'])}}
        self.write('output.json', report)
        self.selection = self.write('selection.json', {'takes': {}, 'basis_sha256': self.basis_sha})
        return report

    def adopt(self):
        return gate.adopt(self.base/'output.json', self.base/'selection.json', self.selection['sha256'])

    def test_negative_05_static_approval_or_rejected_cannot_adopt(self):
        report = self.output_review()
        for status in ('approved_static_only', 'rejected', 'candidate'):
            current = gate.strict_load(self.base/'current.json')
            current['status'] = status
            report['visual_review']['current_review'] = self.write('current.json', current)
            self.write('output.json', report)
            with self.assertRaises(gate.GateError):
                self.adopt()
            self.assertEqual(gate.refs.digest(self.base/'selection.json'), self.selection['sha256'])

    def test_negative_06_player_log_is_not_observed_playback(self):
        report = self.output_review()
        report['playback']['video']['kind'] = 'player_started'
        self.write('output.json', report)
        with self.assertRaisesRegex(gate.GateError, 'playback_modality'):
            self.adopt()

    def test_positive_adopt_retains_previous_manifest_and_no_render_claim(self):
        self.output_review()
        result = self.adopt()
        self.assertTrue(result['selection_updated'])
        self.assertFalse(result['film_rendered'])
        self.assertEqual(len(list(self.base.glob('selection.json.*.bak'))), 1)
        with self.assertRaisesRegex(gate.GateError, 'selection_changed'):
            self.adopt()

    def test_negative_observed_events_out_of_order(self):
        report = self.output_review()
        report['observed_events']['release']['frame'] = 0
        self.write('output.json', report)
        with self.assertRaisesRegex(gate.GateError, 'observed_event_order'):
            self.adopt()

    def test_strict_json_duplicate_key(self):
        (self.base/'packet.json').write_text('{"unit":"a","unit":"b"}')
        self.rejects('duplicate_json_key')

    def test_negative_seed_only_even_with_claimed_method_review(self):
        self.unit['repeat_defect'] = 'stiff contact'
        self.unit['previous_core_sha256'] = gate.refs.request_digest({k: v for k, v in self.request.items()
            if k not in {'seed', 'idempotency_key', 'title'}})
        self.request['seed'] = 88
        self.freeze()
        self.rejects('seed_only_retry')

    def test_negative_adoption_cannot_weaken_upstream_basis(self):
        report = self.output_review()
        self.basis['units']['u1']['required_events'] = []
        report['basis'] = self.write('basis.json', self.basis)
        self.write('output.json', report)
        with self.assertRaisesRegex(gate.GateError, 'selection_basis_mismatch'):
            self.adopt()

    def test_negative_candidate_wrong_task(self):
        report = self.output_review()
        current = gate.strict_load(self.base/'current.json')
        current['task_id'] = 'another-task'
        report['visual_review']['current_review'] = self.write('current.json', current)
        self.write('output.json', report)
        with self.assertRaisesRegex(gate.GateError, 'candidate_task_mismatch'):
            self.adopt()

    def mcp_adapter(self, quote):
        calls = []
        inventory = {'create_tool': 'video.create', 'tools': ['video.create', 'video.preflight'],
                     'capabilities': {'mock': True}}
        def call(config, tool, args):
            calls.append((tool, copy.deepcopy(args)))
            return quote if tool == 'video.preflight' else {'video_task_id': 'mock-one'}
        adapter = gate.MCPAdapter.__new__(gate.MCPAdapter)
        adapter.config = None
        adapter.inventory = None
        adapter.client = SimpleNamespace(check_mcp_tools=lambda config: inventory,
            select_route=lambda *args: ('h3-fasth3', 'minimax-h3-ref2va', {}),
            validate_capability=lambda request, selected: None,
            validate_tool=lambda inventory, tool, args: None, call_mcp_tool=call)
        return adapter, calls, inventory

    def test_adapter_sends_frozen_create_and_strips_creation_metadata(self):
        adapter, calls, inventory = self.mcp_adapter({'can_submit': True})
        self.request['title'] = '保护推送验证'
        def validate(inv, tool, args):
            if tool == 'video.preflight':
                self.assertNotIn('title', args)
                self.assertNotIn('idempotency_key', args)
            else:
                self.assertEqual(args['title'], '保护推送验证')
        adapter.client.validate_tool = validate
        adapter.preflight(self.request)
        adapter.create(self.request)
        self.assertEqual(calls, [('video.preflight', {k: v for k, v in self.request.items()
                               if k not in {'idempotency_key', 'title'}}), ('video.create', self.request)])

    def test_adapter_blocks_unknown_readiness_and_missing_tool(self):
        for quote in ({'can_submit': False}, {}):
            adapter, calls, inventory = self.mcp_adapter(quote)
            with self.assertRaisesRegex(gate.GateError, 'service_preflight_not_ready'):
                adapter.preflight(self.request)
            self.assertEqual([x[0] for x in calls], ['video.preflight'])
            with self.assertRaisesRegex(gate.GateError, 'preflight_required'):
                adapter.create(self.request)
        adapter, calls, inventory = self.mcp_adapter({'can_submit': True})
        inventory['tools'].remove('video.preflight')
        with self.assertRaisesRegex(gate.GateError, 'registered_preflight_missing'):
            adapter.preflight(self.request)
        self.assertEqual(calls, [])

    def test_adapter_accepts_local_h3_native_preflight_but_rejects_false_or_incomplete(self):
        self.request.update(route='h3-fasth3', model='minimax-h3-ref2va', image_guidance='first_frame')
        quote = {'status': 'validated', 'input_digest': 'sha256:' + 'a' * 64,
                 'resolved_output': {'frames': 107, 'width': 768, 'height': 1344},
                 'prompt': self.request['prompt'], 'image_guidance': 'first_frame'}
        adapter, calls, _ = self.mcp_adapter(quote)
        adapter.preflight(self.request)
        adapter.create(self.request)
        self.assertEqual(calls[-1][0], 'video.create')
        for invalid in ({**quote, 'can_submit': False}, {**quote, 'status': 'queued'},
                        {**quote, 'resolved_output': {}}, {**quote, 'prompt': 'different'},
                        {**quote, 'error': 'not ready'}):
            adapter, calls, _ = self.mcp_adapter(invalid)
            with self.assertRaisesRegex(gate.GateError, 'service_preflight_not_ready'):
                adapter.preflight(self.request)
            self.assertEqual(len(calls), 1)
        self.request['route'] = 'fal'
        adapter, _, _ = self.mcp_adapter(quote)
        with self.assertRaisesRegex(gate.GateError, 'service_preflight_not_ready'):
            adapter.preflight(self.request)

    def test_adapter_cannot_send_changed_request_or_reuse_admission(self):
        adapter, calls, inventory = self.mcp_adapter({'can_submit': True})
        adapter.preflight(self.request)
        changed = {**self.request, 'prompt': 'changed'}
        with self.assertRaisesRegex(gate.GateError, 'preflight_required'):
            adapter.create(changed)
        adapter.create(self.request)
        with self.assertRaisesRegex(gate.GateError, 'preflight_required'):
            adapter.create(self.request)
        self.assertEqual(len([x for x in calls if x[0] == 'video.create']), 1)

    def test_adoption_cannot_drop_actor_or_substitute_identity(self):
        report = self.output_review()
        report['visual_review']['actors']['A']['original_identity'] = self.audit['references'][1]['source']
        self.write('output.json', report)
        with self.assertRaisesRegex(gate.GateError, 'output_actor_baseline_changed'):
            self.adopt()


if __name__ == '__main__':
    unittest.main()
