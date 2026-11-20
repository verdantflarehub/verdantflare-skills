import importlib.util
import json
import copy
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'validate_reference_reuse.py'
spec = importlib.util.spec_from_file_location('reference_reuse', SCRIPT)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


class ReferenceReuseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.source = self.write('source.bin', b'failed source video')
        self.frame = self.write('frame.bin', b'frame from rejected source')
        self.review = self.write('review.json', {'decision': 'needs_revision'})
        self.item = {'key': 'POSE', 'role': 'space', 'source': self.source,
                     'input': self.frame, 'source_review': self.review, 'derivation': 'frame'}
        self.imports = [{'key': 'POSE', 'sha256': self.frame['sha256']}]

    def write(self, name, content):
        p = self.base / name
        p.write_bytes(content if isinstance(content, bytes) else json.dumps(content).encode())
        return {'path': name, 'sha256': mod.digest(p)}

    def check(self):
        return mod.validate(self.imports, {'references': [self.item]}, self.base)

    def test_rejected_full_frame_cannot_be_relabelled_spatial(self):
        self.assertIn('Rejected full frame', self.check()['errors'][0])

    def test_crop_without_independent_review_fails(self):
        self.item['derivation'] = 'crop'
        self.assertFalse(self.check()['record_check_passed'])

    def scope(self):
        self.item['derivation'] = 'crop'
        record = {'decision': 'usable_for_scope', 'role': 'space',
                  'input_sha256': self.frame['sha256'], 'source_sha256': self.source['sha256'],
                  'excluded_defects': ['wrong jewellery outside crop'], 'remaining_conflicts': []}
        self.item['scope_review'] = self.write('scope.json', record)
        return record

    def test_independently_reviewed_crop_only_passes_record_check(self):
        self.scope()
        r = self.check()
        self.assertTrue(r['record_check_passed'])
        self.assertFalse(r['artistic_approval'])
        self.assertTrue(r['warnings'])

    def test_changed_input_bytes_fail(self):
        self.scope()
        (self.base / 'frame.bin').write_bytes(b'changed')
        self.assertFalse(self.check()['record_check_passed'])

    def test_changed_import_or_missing_audit_fails(self):
        self.scope()
        self.imports.append({'key': 'UNREVIEWED', 'sha256': '0' * 64})
        self.assertFalse(self.check()['record_check_passed'])

    def test_other_crop_review_cannot_approve_current_input(self):
        r = self.scope()
        r['input_sha256'] = '0' * 64
        self.item['scope_review'] = self.write('scope.json', r)
        self.assertFalse(self.check()['record_check_passed'])

    def test_remaining_conflict_is_not_erased_by_scope_label(self):
        r = self.scope()
        r['remaining_conflicts'] = ['wrong ring remains visible']
        self.item['scope_review'] = self.write('scope.json', r)
        self.assertFalse(self.check()['record_check_passed'])

    def request_case(self):
        imports = [{'key': 'MOTHER', 'artifact_id': 'face', 'sha256': 'a' * 64},
                   {'key': 'FRAME', 'artifact_id': 'frame', 'sha256': 'b' * 64}]
        request = {'project_id': 'project', 'source_artifact_id': 'frame',
                   'reference_artifact_ids': ['face']}
        locks = {'project_id': 'project', 'required_references': [
            {'key': 'MOTHER', 'artifact_id': 'face', 'sha256': 'a' * 64,
             'basis': 'original user identity reference'}]}
        return imports, request, locks

    def test_actual_request_omits_identity_despite_complete_import_list(self):
        imports, request, locks = self.request_case()
        request['reference_artifact_ids'] = []
        errors, target = mod.validate_request(imports, request, locks)
        self.assertTrue(any('fixed reference missing' in e for e in errors))
        self.assertIsNone(target)

    def test_actual_request_cannot_hide_an_extra_failed_reference(self):
        imports, request, locks = self.request_case()
        request['reference_artifact_ids'].append('failed-other-shot')
        self.assertTrue(mod.validate_request(imports, request, locks)[0])

    def test_changed_identity_mapping_and_wrong_project_are_rejected(self):
        imports, request, locks = self.request_case()
        imports[0]['sha256'] = 'c' * 64
        request['project_id'] = 'other-project'
        errors, _ = mod.validate_request(imports, request, locks)
        self.assertEqual(len(errors), 2)

    def test_verified_edit_target_does_not_allow_failed_context(self):
        imports, request, locks = self.request_case()
        errors, target = mod.validate_request(imports, request, locks)
        self.assertEqual(errors, [])
        self.assertEqual(target, 'FRAME')
        self.item['key'] = 'FRAME'
        self.imports[0]['key'] = 'FRAME'
        self.item['role'] = 'repair_target'
        result = mod.validate(self.imports, {'references': [self.item]}, self.base,
                              repair_target_key=target)
        self.assertTrue(result['record_check_passed'])
        self.assertFalse(result['artistic_approval'])
        self.item['role'] = 'material_reference'
        result = mod.validate(self.imports, {'references': [self.item]}, self.base,
                              repair_target_key=target)
        self.assertFalse(result['record_check_passed'])

    def test_second_character_identity_is_not_optional_when_locked(self):
        imports, request, locks = self.request_case()
        locks['required_references'].append(
            {'key': 'BOY', 'artifact_id': 'boy', 'sha256': 'c' * 64,
             'basis': 'original child reference'})
        errors, _ = mod.validate_request(imports, request, locks)
        self.assertTrue(any('BOY' in e for e in errors))

    def test_face_repair_checks_real_source_and_failed_target(self):
        imports, _, locks = self.request_case()
        request = {'project_id': 'project', 'target_artifact_id': 'frame',
                   'source_identity_artifact_id': 'face'}
        self.assertEqual(mod.validate_request(imports, request, locks), ([], 'FRAME'))
        request['source_identity_artifact_id'] = 'wrong-generated-face'
        errors, target = mod.validate_request(imports, request, locks)
        self.assertTrue(any('fixed reference missing' in e for e in errors))
        self.assertIsNone(target)

    def test_face_repair_rejects_mixed_missing_and_self_identity(self):
        imports, _, locks = self.request_case()
        for request in [
            {'target_artifact_id': 'frame'},
            {'target_artifact_id': 'frame', 'source_identity_artifact_id': 'face',
             'reference_artifact_ids': ['face']},
            {'target_artifact_id': 'frame', 'source_identity_artifact_id': 'frame'},
        ]:
            request['project_id'] = 'project'
            errors, target = mod.validate_request(imports, request, locks)
            self.assertTrue(errors)
            self.assertIsNone(target)

    def test_inpaint_mask_is_part_of_actual_input_mapping(self):
        imports, request, locks = self.request_case()
        request['mask_artifact_id'] = 'mask'
        self.assertTrue(mod.validate_request(imports, request, locks)[0])
        imports.append({'key': 'MASK', 'artifact_id': 'mask', 'sha256': 'c' * 64})
        self.assertEqual(mod.validate_request(imports, request, locks), ([], 'FRAME'))

    def freeze(self, imports, request, locks):
        args = request.get('arguments', request)
        slots, _ = mod.input_slots(args)
        by_id = {x['artifact_id']: x for x in imports}
        locks['input_bindings'] = [
            {**x, 'key': by_id[x['artifact_id']]['key'],
             'sha256': by_id[x['artifact_id']]['sha256']} for x in slots]
        locks['request_sha256'] = mod.request_digest(request)

    def test_duplicate_cannot_hide_behind_equal_reference_sets(self):
        imports, request, locks = self.request_case()
        request['reference_artifact_ids'].append('face')
        self.assertTrue(mod.validate_request(imports, request, locks)[0])

    def test_import_order_is_not_model_order(self):
        imports, request, locks = self.request_case()
        self.freeze(imports, request, locks)
        self.assertEqual(mod.validate_request(list(reversed(imports)), request, locks, True), ([], 'FRAME'))

    def test_equal_sets_with_swapped_character_slots_are_rejected(self):
        imports, request, locks = self.request_case()
        imports.append({'key': 'BOY', 'artifact_id': 'boy', 'sha256': 'c' * 64})
        request['reference_artifact_ids'].append('boy')
        self.freeze(imports, request, locks)
        request['reference_artifact_ids'].reverse()
        # Updating the generic request hash cannot conceal stale role/slot bindings.
        locks['request_sha256'] = mod.request_digest(request)
        self.assertTrue(any('slots differ' in e for e in mod.validate_request(imports, request, locks, True)[0]))

    def test_mask_and_source_cannot_swap_with_same_reference_set(self):
        imports, request, locks = self.request_case()
        imports.append({'key': 'MASK', 'artifact_id': 'mask', 'sha256': 'c' * 64})
        request['mask_artifact_id'] = 'mask'
        self.freeze(imports, request, locks)
        request['mask_artifact_id'], request['source_artifact_id'] = request['source_artifact_id'], request['mask_artifact_id']
        self.assertTrue(mod.validate_request(imports, request, locks, True)[0])

    def test_identity_role_cannot_be_renamed_without_updating_fixed_anchor(self):
        imports, request, locks = self.request_case()
        imports[0]['key'] = 'BOY'
        self.assertTrue(any('role key differs' in e for e in mod.validate_request(imports, request, locks)[0]))

    def test_request_digest_invalidates_changed_model_prompt_and_transform(self):
        imports, request, locks = self.request_case()
        self.freeze(imports, request, locks)
        for field, value in [('model', 'other-model'), ('prompt', 'different actor'),
                             ('crop', [0, 0, 1, 1]), ('duration_seconds', 5)]:
            with self.subTest(field=field):
                changed = {**request, field: value}
                self.assertTrue(any('frozen request' in e for e in mod.validate_request(imports, changed, locks, True)[0]))

    def test_tool_name_is_part_of_saved_request_digest(self):
        imports, request, locks = self.request_case()
        journal = {'name': 'image.edit', 'arguments': request}
        self.freeze(imports, journal, locks)
        self.assertFalse(mod.validate_request(imports, journal, locks, True)[0])
        journal['name'] = 'image.inpaint'
        self.assertTrue(mod.validate_request(imports, journal, locks, True)[0])

    def test_video_inputs_keep_purpose_order_and_all_media_groups(self):
        imports, _, locks = self.request_case()
        request = {'project_id': 'project', 'references': {
            'images': [{'artifact_id': 'face', 'purpose': 'identity'},
                       {'artifact_id': 'frame', 'purpose': 'scene'}],
            'audios': [{'artifact_id': 'audio', 'purpose': 'dialogue'}]}}
        imports.append({'key': 'AUDIO', 'artifact_id': 'audio', 'sha256': 'c' * 64})
        self.freeze(imports, request, locks)
        self.assertEqual(mod.validate_request(imports, request, locks, True), ([], None))
        for mutation in ('purpose', 'order', 'audio'):
            changed = copy.deepcopy(request)
            if mutation == 'purpose':changed['references']['images'][0]['purpose'] = 'style'
            elif mutation == 'order':changed['references']['images'].reverse()
            else:changed['references']['audios'] = []
            with self.subTest(mutation=mutation):
                self.assertTrue(mod.validate_request(imports, changed, locks, True)[0])

    def test_intentional_repeated_reference_requires_exact_slot_evidence(self):
        imports, request, locks = self.request_case()
        request['reference_artifact_ids'].append('face')
        self.freeze(imports, request, locks)
        self.assertEqual(mod.validate_request(imports, request, locks, True), ([], 'FRAME'))

    def test_production_mode_requires_frozen_request_and_slots(self):
        imports, request, locks = self.request_case()
        self.assertFalse(mod.validate_request(imports, request, locks)[0])
        self.assertTrue(mod.validate_request(imports, request, locks, True)[0])

    def test_malformed_inputs_fail_without_traceback(self):
        imports, request, locks = self.request_case()
        for bad in (None, [], {'arguments': []}, {**request, 'reference_artifact_ids': 'face'},
                    {**request, 'reference_artifact_ids': [{}]}):
            with self.subTest(value=bad):
                self.assertTrue(mod.validate_request(imports, bad, locks)[0])
        self.assertFalse(mod.validate([{'key': []}], {'references': []}, self.base)['record_check_passed'])

    def test_record_only_success_cannot_pass_production_cli(self):
        self.scope()
        self.write('imports.json', self.imports)
        self.write('audit.json', {'references': [self.item]})
        cmd = [sys.executable, str(SCRIPT), str(self.base/'imports.json'), str(self.base/'audit.json')]
        record = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(record.returncode, 0)
        self.assertFalse(json.loads(record.stdout)['production_input_check_passed'])
        production = subprocess.run(cmd + ['--require-request'], capture_output=True, text=True)
        self.assertEqual(production.returncode, 1)
        self.assertFalse(json.loads(production.stdout)['production_input_check_passed'])

    def test_production_cli_pass_is_bound_to_exact_request_and_bytes(self):
        imports, request, locks = self.request_case()
        approved = self.write('approved.json', {'decision': 'approved'})
        audit = {'references': []}
        for item in imports:
            file = self.write(item['key']+'.bin', item['key'].encode())
            item['sha256'] = file['sha256']
            audit['references'].append({'key': item['key'], 'role': item['key'],
                                       'source': file, 'input': file, 'source_review': approved})
        locks['required_references'][0]['sha256'] = imports[0]['sha256']
        self.freeze(imports, request, locks)
        for filename, value in [('imports.json', imports), ('audit.json', audit),
                                ('request.json', request), ('locks.json', locks)]:
            self.write(filename, value)
        cmd = [sys.executable, str(SCRIPT), str(self.base/'imports.json'), str(self.base/'audit.json'),
               '--request', str(self.base/'request.json'), '--locks', str(self.base/'locks.json'), '--require-request']
        run = subprocess.run(cmd, capture_output=True, text=True)
        result = json.loads(run.stdout)
        self.assertEqual(run.returncode, 0, result)
        self.assertTrue(result['production_input_check_passed'])
        self.assertEqual(result['verified_request_sha256'], locks['request_sha256'])
        self.assertFalse(result['artistic_approval'])
        (self.base/'MOTHER.bin').write_bytes(b'changed identity pixels')
        run = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(run.returncode, 1)
        self.assertFalse(json.loads(run.stdout)['production_input_check_passed'])


if __name__ == '__main__':
    unittest.main()
