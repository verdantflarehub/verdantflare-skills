"""Acceptance preparation must preserve obligations and never invent success."""
import contextlib
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1] / 'run.py'
SPEC = importlib.util.spec_from_file_location('acceptance_preflight', SOURCE)
v = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(v)


class PreflightTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[5] / '.tmp')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.exam_path = self.root / 'synthetic-exam.md'
        cases = sorted({case for values in v.CASES.values() for case in values})
        self.exam_path.write_text('# Synthetic test fixture\n\n## 2. Cases\n\n'
                                  + '\n'.join(f'- {case}: fixture assertion' for case in cases)
                                  + '\n\n## 3. Evidence\n', encoding='utf-8')
        self.exam = v.exam_info(self.exam_path)
        self.matrix = v.new_matrix(self.exam)
        self.matrix_path = self.root / 'matrix.json'
        self.matrix_path.write_bytes(v.encode(self.matrix))

    def call(self, *args):
        stream = io.StringIO()
        with contextlib.redirect_stdout(stream):
            code = v.main(list(args))
        return code, json.loads(stream.getvalue())

    def report(self, **values):
        return v.report(self.matrix, self.exam, phase='candidate', scope_os='macos',
                        scope_hosts=values.get('hosts', 'workbuddy'),
                        selected=values.get('groups', ','.join(v.CASES)))

    def evidence(self, files=None):
        raw = b'{"status":"pass","claimed_desktop":true}\n'
        p = self.root / 'claimed.json'
        p.write_bytes(raw)
        entries = files if files is not None else [{'path': p.name, 'bytes': len(raw), 'sha256': v.digest(raw)}]
        manifest = self.root / 'evidence.json'
        manifest.write_bytes(v.encode({'schema': 'vf.desktop-skill-validation.evidence/v1', 'files': entries}))
        return manifest

    def test_full_obligations_and_priority_scope_remain_distinct(self):
        v.check_matrix(self.matrix, self.exam)
        summary, rows = self.report()
        self.assertEqual(summary['full_matrix'], {'environments': 10, 'skill_cells': 110, 'business_rows': 60})
        self.assertEqual(summary['selected_scope'], {'environments': 1, 'skill_cells': 11, 'business_rows': 6})
        self.assertEqual(sum(r['record_type'] == 'skill_cell' for r in rows), 110)
        self.assertEqual(sum(r['record_type'] == 'business_row' for r in rows), 60)
        self.assertFalse(any(r['status'] == 'pass' for r in rows))
        wb = [r for r in rows if r['environment_id'] == 'macos-workbuddy']
        self.assertTrue(wb and all(r['status'] == 'blocked' for r in wb))
        self.assertTrue(all(not r['execution_admitted'] for r in rows))

    def test_four_host_scope_preserves_suspended_and_delegated_rows(self):
        summary, rows = self.report(hosts='workbuddy,codex,deepseek,antigravity')
        self.assertEqual(summary['selected_scope']['skill_cells'], 44)
        self.assertEqual(summary['selected_scope']['business_rows'], 24)
        for row in rows:
            if row['host_profile'] == 'claude':
                self.assertEqual(row['status'], 'not_run')
                self.assertEqual(row['reason_code'], 'CLAUDE_DESKTOP_PAUSED_A21')
            elif row['os'] == 'windows':
                self.assertEqual(row['reason_code'], 'WINDOWS_DELEGATED_TO_MENGSK_A20')

    def test_paused_target_cannot_be_resumed_by_flag(self):
        with self.assertRaisesRegex(v.ValidationError, 'CLAUDE_DESKTOP_PAUSED'):
            self.report(hosts='workbuddy,claude')

    def test_unknown_duplicate_and_empty_selections_rejected(self):
        for groups in ('host,hosst', '', 'host,host'):
            with self.subTest(groups=groups), self.assertRaises(v.ValidationError):
                self.report(groups=groups)
        for hosts in ('workbudy', '', 'workbuddy,workbuddy'):
            with self.subTest(hosts=hosts), self.assertRaises(v.ValidationError):
                self.report(hosts=hosts)

    def test_group_selection_does_not_omit_other_requirements(self):
        summary, rows = self.report(groups='host')
        self.assertEqual(sum(r['record_type'] == 'business_row' for r in rows), 60)
        self.assertTrue(all(r['status'] == 'not_run' for r in rows if r['record_type'] == 'business_row'))
        self.assertEqual(summary['exit_code'], 2)

    def test_inventory_does_not_fabricate_per_skill_applicability(self):
        summary, rows = self.report()
        self.assertFalse(summary['case_applicability_frozen'])
        for row in rows:
            self.assertFalse(row['applicability_verified'])
            self.assertNotIn('required_assertions', row)

    def test_deleted_duplicate_unknown_and_wrong_coverage_rejected(self):
        for field in ('skill_cells', 'business_rows', 'environments'):
            for operation in ('delete', 'duplicate', 'unknown'):
                value = copy.deepcopy(self.matrix)
                if operation == 'delete':
                    value[field].pop()
                elif operation == 'duplicate':
                    value[field][1] = copy.deepcopy(value[field][0])
                else:
                    value[field][0]['environment_id'] = 'macos-nonexistent'
                with self.subTest(field=field, operation=operation), self.assertRaises(v.ValidationError):
                    v.check_matrix(value, self.exam)

    def test_forged_passes_commands_and_verdicts_are_not_matrix_inputs(self):
        for location in ('root', 'environment', 'cell', 'business'):
            value = copy.deepcopy(self.matrix)
            target = {'root': value, 'environment': value['environments'][0],
                      'cell': value['skill_cells'][0], 'business': value['business_rows'][0]}[location]
            for field in ('status', 'command', 'verdict'):
                target[field] = 'pass'
                with self.subTest(location=location, field=field), self.assertRaises(v.ValidationError):
                    v.check_matrix(value, self.exam)
                del target[field]

    def test_skill_and_package_sets_cannot_be_shrunk(self):
        for field in ('skills', 'package_sha256'):
            value = copy.deepcopy(self.matrix)
            value[field].pop() if field == 'skills' else value[field].pop(v.SKILLS[0])
            with self.assertRaises(v.ValidationError):
                v.check_matrix(value, self.exam)

    def test_exam_drift_and_duplicate_or_missing_assertions_rejected(self):
        changed = dict(self.exam, sha256='0' * 64)
        with self.assertRaisesRegex(v.ValidationError, 'EXAM_DIGEST_CHANGED'):
            v.check_matrix(self.matrix, changed)
        original = self.exam_path.read_text()
        for text in (original.replace('- HOST-01:', '- HOST-02:'), original.replace('## 3.', '- HOST-01: dup\n## 3.')):
            self.exam_path.write_text(text)
            with self.assertRaisesRegex(v.ValidationError, 'EXAM_CASESET_CHANGED'):
                v.exam_info(self.exam_path)

    def test_exam_accepts_existing_unbulleted_evidence_assertion(self):
        self.exam_path.write_text(self.exam_path.read_text().replace('- EVIDENCE-01:', 'EVIDENCE-01:'))
        self.assertIn('EVIDENCE-01', v.exam_info(self.exam_path)['cases'])

    def test_duplicate_keys_nonfinite_and_excessive_nesting_rejected(self):
        for raw in (b'{"a":1,"a":2}', b'{"x":NaN}', b'{"x":1e9999}', b'9' * 5000,
                    b'[' * 2000 + b']' * 2000, b'\xff'):
            with self.assertRaises(v.ValidationError):
                v.strict_json(raw)

    def test_evidence_pass_label_remains_untrusted_bytes(self):
        info = v.evidence_info(self.evidence())
        self.assertEqual(info['files_verified'], 1)
        self.assertFalse(info['native_source_verified'])
        self.assertFalse(info['independent_verdict_verified'])
        summary, rows = v.report(self.matrix, self.exam, phase='candidate', scope_os='macos',
                                 scope_hosts='workbuddy', selected='host', evidence=info)
        self.assertEqual(summary['exit_code'], 2)
        self.assertFalse(any(r['status'] == 'pass' for r in rows))

    def test_integer_digit_limit_does_not_depend_on_python_default(self):
        for sign in (b'', b'-'):
            raw = sign + b'9' * v.JSON_MAX_INTEGER_DIGITS
            self.assertIs(type(v.strict_json(raw)), int)
            with self.assertRaisesRegex(v.ValidationError, '^JSON_INTEGER_LIMIT$'):
                v.strict_json(raw + b'9')

    def test_json_depth_boundary_for_arrays_objects_and_mixed_containers(self):
        for prefix, suffix in ((b'[', b']'), (b'{"x":', b'}'), (b'[{"x":', b'}]')):
            layers = v.JSON_MAX_DEPTH // (prefix.count(b'[') + prefix.count(b'{'))
            with self.subTest(prefix=prefix):
                v.strict_json(prefix * layers + b'0' + suffix * layers)
                with self.assertRaisesRegex(v.ValidationError, '^JSON_NESTING_LIMIT$'):
                    v.strict_json(prefix * (layers + 1) + b'0' + suffix * (layers + 1))

    def test_json_depth_ignores_string_delimiters_and_escaped_quotes(self):
        value = '[{' * 2000 + '\\"' + '}]' * 2000 + '\\\\'
        raw = json.dumps({'x': value}, ensure_ascii=False).encode('utf-8')
        self.assertEqual(v.strict_json(raw), {'x': value})
        with self.assertRaisesRegex(v.ValidationError, '^JSON_NESTING_LIMIT$'):
            v.strict_json(b'["escaped\\\"quote",' + b'[' * v.JSON_MAX_DEPTH + b'0'
                          + b']' * v.JSON_MAX_DEPTH + b']')

    def test_wrong_evidence_hash_size_and_numeric_types_rejected(self):
        for change in ({'sha256': '0' * 64}, {'bytes': 0}, {'bytes': True}, {'sha256': True}):
            manifest = self.evidence()
            value = json.loads(manifest.read_text())
            value['files'][0].update(change)
            manifest.write_bytes(v.encode(value))
            with self.subTest(change=change), self.assertRaises(v.ValidationError):
                v.evidence_info(manifest)

    def test_evidence_traversal_absolute_backslash_and_aliases_rejected(self):
        for name in ('../claimed.json', '/claimed.json', './claimed.json', 'a//b', 'a\\b', 'C:/claimed.json'):
            manifest = self.evidence([{'path': name, 'bytes': 0, 'sha256': '0' * 64}])
            with self.subTest(name=name), self.assertRaises(v.ValidationError):
                v.evidence_info(manifest)
        manifest = self.evidence()
        value = json.loads(manifest.read_text())
        value['files'].append(dict(value['files'][0], path='CLAIMED.json'))
        manifest.write_bytes(v.encode(value))
        with self.assertRaisesRegex(v.ValidationError, 'EVIDENCE_PATH_DUPLICATE'):
            v.evidence_info(manifest)

    def test_evidence_links_and_nonregular_files_rejected(self):
        manifest = self.evidence()
        real = self.root / 'claimed.json'
        real.rename(self.root / 'original.json')
        real.symlink_to(self.root / 'original.json')
        with self.assertRaisesRegex(v.ValidationError, 'LINK_NOT_ALLOWED'):
            v.evidence_info(manifest)
        real.unlink()
        os.link(self.root / 'original.json', real)
        with self.assertRaisesRegex(v.ValidationError, 'REGULAR_FILE_REQUIRED'):
            v.evidence_info(manifest)
        real.unlink()
        os.mkfifo(real)
        with self.assertRaisesRegex(v.ValidationError, 'REGULAR_FILE_REQUIRED'):
            v.evidence_info(manifest)

    def test_streaming_content_mutation_is_detected(self):
        manifest = self.evidence()
        original = os.read
        mutated = False

        def read(fd, size):
            nonlocal mutated
            block = original(fd, size)
            if block and not mutated:
                mutated = True
                (self.root / 'claimed.json').write_bytes(b'changed')
            return block

        # Mutate the payload after manifest parsing, rather than its first read.
        original_read = v.read_file

        def file_read(path, **kwargs):
            if Path(path).name == 'claimed.json':
                with patch.object(os, 'read', read):
                    return original_read(path, **kwargs)
            return original_read(path, **kwargs)

        with patch.object(v, 'read_file', file_read), self.assertRaisesRegex(v.ValidationError, 'INPUT_CHANGED'):
            v.evidence_info(manifest)

    def test_cli_writes_private_full_report_and_verifiable_manifest(self):
        output = self.root / 'results'
        code, summary = self.call('--matrix', str(self.matrix_path), '--exam', str(self.exam_path), '--output', str(output))
        self.assertEqual(code, 2)
        self.assertFalse(summary['desktop_acceptance'])
        self.assertEqual(summary['formal_skill_cells_passed'], 0)
        self.assertEqual(v.evidence_info(output / 'manifest.json')['files_verified'], 2)
        self.assertEqual(output.stat().st_mode & 0o777, 0o700)
        for child in output.iterdir():
            self.assertEqual(child.stat().st_mode & 0o777, 0o600)
        results = [json.loads(line) for line in (output / 'results.jsonl').read_text().splitlines()]
        for result in results:
            self.assertIsNone(result['request_id'])
            self.assertIn('request_id', result['unobserved_fields'])
            self.assertEqual(result['exam_sha256'], self.exam['sha256'])
        before = {p.name: p.read_bytes() for p in output.iterdir()}
        code, error = self.call('--matrix', str(self.matrix_path), '--exam', str(self.exam_path), '--output', str(output))
        self.assertEqual((code, error['code']), (1, 'NEW_OUTPUT_REQUIRED'))
        self.assertEqual(before, {p.name: p.read_bytes() for p in output.iterdir()})

    def test_init_matrix_is_not_formal_success_and_never_overwrites(self):
        output = self.root / 'new-matrix.json'
        code, result = self.call('--init-matrix', str(output), '--exam', str(self.exam_path))
        self.assertEqual(code, 2)
        self.assertFalse(result['execution_admitted'])
        v.check_matrix(json.loads(output.read_text()), self.exam)
        before = output.read_bytes()
        code, error = self.call('--init-matrix', str(output), '--exam', str(self.exam_path))
        self.assertEqual(code, 1)
        self.assertEqual(error['code'], 'NEW_OUTPUT_REQUIRED')
        self.assertEqual(before, output.read_bytes())

    def test_bad_arguments_and_io_errors_do_not_echo_private_values(self):
        secret_marker = 'sensitive-test-marker'
        for args in (('--unknown', secret_marker),
                     ('--matrix', str(self.root / secret_marker), '--output', str(self.root / 'out')),
                     ('--init-matrix', str(self.root / 'init'), '--phase', secret_marker)):
            stream = io.StringIO()
            with contextlib.redirect_stdout(stream):
                code = v.main(['--exam', str(self.exam_path), *args])
            self.assertEqual(code, 1)
            self.assertNotIn(secret_marker, stream.getvalue())
            self.assertNotIn(str(self.root), stream.getvalue())


if __name__ == '__main__':
    unittest.main()
