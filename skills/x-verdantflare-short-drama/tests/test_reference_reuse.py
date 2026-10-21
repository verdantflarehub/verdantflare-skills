import importlib.util
import json
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


if __name__ == '__main__':
    unittest.main()
