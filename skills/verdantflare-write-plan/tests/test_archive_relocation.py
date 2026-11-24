"""Synthetic archive lifecycle tests. No actual plan is approved or moved."""
from dataclasses import replace
import copy
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import archive_relocation as ar
import evidence_view as ev
import final_decisions as fd
import plan_lint as pl
import process_receipts as pr
from delegated_policy import PolicyError
import test_final_gate as fixture
from test_final_decisions import human
from test_plan_lint import run_cli


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.f = fixture.FinalGateTests(); self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.root = self.f.root.resolve(); self.plan = self.f.plan.resolve()
        self.plan.write_text(self.plan.read_text().replace('计划状态：进行中', '计划状态：已完成')
                            .replace('- [ ] T5.1', '- [x] T5.1')
                            .replace('4/5 项（80%）', '5/5 项（100%）')
                            .replace('T5 人类验收子计划（0/1）', 'T5 人类验收子计划（1/1）'))
        self.f.approve()
        # Unrelated inbound references must be rewritten without moving the file.
        self.index = self.root / 'README.md'
        self.index.write_text('# Index\n\n[Plan](docs/plan/' + self.plan.name + ')\n')

    def prepare(self):
        return pl.prepare_archive(self.plan)

    def apply(self, prepared=None):
        prepared = prepared or self.prepare()
        mapping = json.loads(pr.read_content(self.root, prepared['map']))
        moves = {e['source']: e['destination'] for e in mapping['entries'] if e['source'] != e['destination']}
        for entry in mapping['entries']:
            original = json.loads(pr.read_content(self.root, entry['original']))['text'].encode()
            result = ar.relocate_markdown(original, self.root, entry['source'], entry['destination'], moves)
            if entry['source'] == mapping['active_plan_path']:
                result = ar.attach_header(result, prepared['header'])
            target = self.root / entry['destination']; target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(result)
            if entry['source'] != entry['destination']: (self.root / entry['source']).unlink()
        self.archived = self.root / mapping['archive_plan_path']
        return prepared, mapping

    def check(self):
        issues, rc = pl.check_plan(self.archived, 'archive')
        return {i.code for i in issues}, rc

    def test_complete_archive_replays_original_receipts_and_live_source(self):
        prepared, mapping = self.apply()
        codes, rc = self.check()
        self.assertEqual((codes, rc), (set(), 0))
        self.assertFalse(self.plan.exists())
        self.assertIn('docs/archive/plan/', self.index.read_text())
        self.assertIsNone(ev.original(self.plan))
        self.assertTrue(all(e['source'].endswith('.md') for e in mapping['entries']))

    def test_prepare_cli_does_not_move_or_mark_files_complete(self):
        original = self.plan.read_bytes()
        rc, output, _ = run_cli('prepare-archive', str(self.plan), '--json')
        self.assertEqual(rc, 0, output)
        self.assertTrue(json.loads(output)['ok'])
        self.assertEqual(self.plan.read_bytes(), original)
        self.assertFalse((self.root / 'docs/archive/plan' / self.plan.name).exists())
        self.assertNotIn(str(self.root), output)

    def test_incomplete_status_tasks_or_active_change_cannot_prepare(self):
        original = self.plan.read_text()
        self.plan.write_text(original.replace('计划状态：已完成', '计划状态：进行中'))
        with self.assertRaises(PolicyError): self.prepare()
        self.plan.write_text(original.replace('- [x] T5.1', '- [ ] T5.1'))
        with self.assertRaises(PolicyError): self.prepare()
        self.plan.write_text(original)
        change = self.root / 'docs/changes/change_20261118_extra.md'; change.parent.mkdir(exist_ok=True)
        change.write_text('# Change\n\n变更状态：进行中\n关联任务：[Plan](../plan/' + self.plan.name + ')\n')
        with self.assertRaises(PolicyError): self.prepare()

    def test_no_human_final_reply_or_qualified_reply_cannot_prepare(self):
        source = self.f.base.provider.return_value
        self.f.base.provider.return_value = replace(source, capture=self.f.base.capture, conversation_capture=b'')
        with self.assertRaises(PolicyError): self.prepare()

    def test_destination_collision_and_symbolic_paths_are_rejected(self):
        target = self.root / 'docs/archive/plan' / self.plan.name
        target.parent.mkdir(parents=True, exist_ok=True); target.write_text('Foreign existing document')
        with self.assertRaises(PolicyError): self.prepare()
        target.unlink(); target.symlink_to(self.plan)
        with self.assertRaises(PolicyError): self.prepare()

    def test_changed_archived_or_rewritten_body_and_extra_header_rejected(self):
        self.apply()
        text = self.archived.read_text()
        for changed in (text + '\nUnreviewed requirement.\n', text.replace('计划状态：已完成', '计划状态：进行中')):
            self.archived.write_text(changed)
            self.assertIn(ar.CODE, self.check()[0])
        self.archived.write_text(text)
        self.index.write_text(self.index.read_text() + '\nUnreviewed insertion.\n')
        self.assertIn(ar.CODE, self.check()[0])

    def test_active_duplicate_and_unmapped_new_review_fail(self):
        _, mapping = self.apply()
        original = next(e for e in mapping['entries'] if e['source'] == mapping['active_plan_path'])
        self.plan.write_text(json.loads(pr.read_content(self.root, original['original']))['text'])
        self.assertIn(ar.CODE, self.check()[0])
        self.plan.unlink()
        source = next(e for e in mapping['entries'] if e['source'].startswith('docs/reviews/'))
        extra = self.root / 'docs/archive/reviews/review_20261118_alpha-gov_t1-r8.md'
        extra.write_bytes((self.root / source['destination']).read_bytes())
        self.assertIn(ar.CODE, self.check()[0])

    def test_relocation_cannot_replace_real_evidence_or_native_sources(self):
        self.apply()
        self.f.f.files['img'].unlink()
        self.assertIn('PL-CP0-IMG', self.check()[0])

    def test_current_user_withdrawal_invalidates_original_approval(self):
        self.apply()
        source = self.f.base.provider.return_value
        conversation = source.conversation_capture + human('撤回批准').replace(b'00:00:10Z', b'00:00:12Z')
        self.f.base.provider.return_value = replace(source, capture=fd.goal_projection(conversation),
                                                    conversation_capture=conversation)
        self.assertIn('PL-DELEGATE-H2', self.check()[0])

    def test_final_physical_readback_catches_edit_during_logical_replay(self):
        self.apply()
        original = pl._check_plan_technical
        def change(*args, **kwargs):
            result = original(*args, **kwargs)
            if args[1] == 'archive-preflight':
                self.index.write_text(self.index.read_text() + '\nChanged mid-run.\n')
            return result
        with patch('plan_lint._check_plan_technical', side_effect=change):
            self.assertIn(ar.CODE, self.check()[0])
        self.assertIsNone(ev.original(self.plan))

    def test_forged_cross_plan_map_and_changed_tool_fail(self):
        prepared, mapping = self.apply()
        forged = dict(mapping, plan_id='other-plan')
        reference = pr.store_bytes(self.root, pr.canonical(forged))
        with self.assertRaises(PolicyError):
            ar.validate(self.root, reference, repository_id=mapping['repository_id'],
                        plan_id=mapping['plan_id'], archived=mapping['archive_plan_path'])
        tool = copy.deepcopy(pr.tool_fingerprint()); tool['version'] = 'different'
        with patch('process_receipts.tool_fingerprint', return_value=tool):
            self.assertIn(ar.CODE, self.check()[0])


class RelocationSyntaxTests(unittest.TestCase):
    def test_links_fragments_queries_reference_images_and_code_preserved(self):
        root = Path('/synthetic-root')
        moves = {'docs/plan/p.md': 'docs/archive/plan/p.md'}
        text = ('# Link\n\n[Plan](../plan/p.md#x) ![Image](../img.png)\n'
                '[ref]: ../plan/p.md?q=x#y\n[Plan][ref]\n'
                '`[example](../plan/p.md)`\n```text\n[example](../plan/p.md)\n```\n')
        result = ar.relocate_markdown(text.encode(), root, 'docs/reviews/r.md', 'docs/archive/reviews/r.md', moves).decode()
        self.assertIn('[Plan](../plan/p.md#x)', result)
        self.assertIn('![Image](../../img.png)', result)
        self.assertIn('[ref]: ../plan/p.md?q=x#y', result)
        self.assertIn('`[example](../plan/p.md)`', result)
        self.assertIn('```text\n[example](../plan/p.md)\n```', result)

    def test_view_is_read_only_scoped_and_exception_safe(self):
        path = Path('/synthetic-root/docs/plan/p.md')
        with self.assertRaises(RuntimeError):
            with ev.validated_markdown({path: b'original'}):
                self.assertTrue(ev.is_file(path))
                self.assertEqual(ev.read_bytes(path), b'original')
                with self.assertRaises(ValueError):
                    with ev.validated_markdown({path: b'other'}): pass
                raise RuntimeError('synthetic interruption')
        self.assertIsNone(ev.original(path))


if __name__ == '__main__':
    unittest.main()
