"""Behavioral regression tests for scaffold and static validation contracts."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import lint_skill as tool


class SkillToolTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='meta-skill-test-')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / 'sample-skill'

    def entry(self, front=None, body='# CSV conversion\n\nConvert CSV input to JSON; reject invalid rows.\n'):
        self.root.mkdir(exist_ok=True)
        front = front or 'name: sample-skill\ndescription: Convert CSV to JSON.'
        (self.root / 'SKILL.md').write_text('---\n' + front + '\n---\n\n' + body, encoding='utf-8')

    def standard(self, sections=('1. 范围与边界', '2. 按任务读取', '3. 工具与命令'), extra=''):
        refs = self.root / 'references'
        refs.mkdir(parents=True, exist_ok=True)
        rows = []
        for dim in tool.DIMENSIONS:
            (refs / f'01.base-{dim}.md').write_text(f'# {dim}\n\nDone.\n', encoding='utf-8')
            rows.append(f'- [{dim}](references/01.base-{dim}.md)')
        heads = [f'## {title}\n\nText.\n' for title in sections]
        heads[1] = heads[1] + '\n' + '\n'.join(rows) + '\n'
        self.entry(body='# Sample\n\n' + '\n'.join(heads) + '\n' + extra)

    def test_default_scaffold_is_standard_layout(self):
        tool.initialize(self.root)
        names = sorted(p.relative_to(self.root).as_posix() for p in self.root.rglob('*.md'))
        self.assertEqual(['SKILL.md'] + sorted(f'references/01.base-{d}.md' for d in tool.DIMENSIONS), names)
        self.assertTrue(tool.lint(self.root)[0])
        errors, warnings = tool.lint(self.root, style='numbered', allow_placeholders=True)
        self.assertEqual([], errors)
        self.assertTrue(warnings)
        self.standard()
        self.assertEqual(([], []), tool.lint(self.root))

    def test_matrix_generation(self):
        root = self.base / 'matrix-skill'
        tool.initialize(root, 'C1.local,C2.remote')
        errors, warnings = tool.lint(root, style='numbered', allow_placeholders=True)
        self.assertEqual([], errors)
        self.assertTrue(warnings)
        for path in root.rglob('*.md'):
            self.assertNotIn('{{', path.read_text(encoding='utf-8'))
            self.assertNotIn('<slug>', path.read_text(encoding='utf-8'))
        self.assertIn('references/C2.remote-rules.md', (root / 'SKILL.md').read_text(encoding='utf-8'))
        (root / 'references/C2.remote-rules.md').unlink()
        self.assertTrue(any('missing C2.remote-rules' in e for e in tool.lint(root, allow_placeholders=True)[0]))

    def test_entry_structure_rules(self):
        self.standard(sections=('1. 编码规范', '2. 范围与边界', '3. 按任务读取', '4. 工具与命令'))
        self.assertEqual(([], []), tool.lint(self.root))
        cases = {
            'H2 sections': dict(sections=('1. 范围与边界', '2. 按任务读取', '3. 工具与命令', '4. 经验教训')),
            'H2 sections must': dict(sections=('1. 工具与命令', '2. 按任务读取', '3. 范围与边界')),
            'characters': dict(extra='x' * tool.MAX_ENTRY_CHARS),
            'code blocks': dict(extra='```text\n' + 'cmd\n' * (tool.MAX_ENTRY_CODE_LINES + 1) + '```\n'),
            'H4': dict(extra='### A\n\n#### B\n'),
            'H3 headings': dict(extra=''.join(f'### H{i}\n\n' for i in range(tool.MAX_ENTRY_H3 + 1))),
        }
        for needle, kwargs in cases.items():
            with self.subTest(needle=needle):
                self.standard(**kwargs)
                errors = tool.lint(self.root)[0]
                self.assertTrue(any(needle in e for e in errors), errors)

    def test_entry_requires_base_links_files_and_clean_root(self):
        self.standard()
        (self.root / 'FAQ.md').write_text('# FAQ\n', encoding='utf-8')
        self.assertTrue(any('FAQ.md' in e for e in tool.lint(self.root)[0]))
        (self.root / 'FAQ.md').unlink()
        (self.root / 'references/01.base-rules.md').unlink()
        self.assertTrue(any('missing 01.base-rules.md' in e for e in tool.lint(self.root)[0]))
        self.standard()
        text = (self.root / 'SKILL.md').read_text(encoding='utf-8').replace('- [rules](references/01.base-rules.md)', '')
        (self.root / 'SKILL.md').write_text(text, encoding='utf-8')
        self.assertTrue(any('must link references/01.base-rules.md' in e for e in tool.lint(self.root)[0]))

    def test_long_reference_warns(self):
        self.standard()
        (self.root / 'references/01.base-rules.md').write_text('# rules\n\n' + 'x' * tool.MAX_REFERENCE_CHARS, encoding='utf-8')
        errors, warnings = tool.lint(self.root)
        self.assertEqual([], errors)
        self.assertTrue(any('01.base-rules.md' in w for w in warnings))

    def test_yaml_quotes_multiline_and_metadata(self):
        self.entry('name: "sample-skill"\ndescription: |\n  Convert CSV.\n  Validate rows.\nmetadata:\n  label: "Data"')
        self.assertEqual(([], []), tool.lint(self.root, structure=False))

    def test_invalid_yaml_and_types(self):
        for front in ('name: sample-skill\ndescription: [unterminated',
                      'name: sample-skill\nname: duplicate\ndescription: test',
                      'name: sample-skill\ndescription: |\n',
                      'name: [sample-skill]\ndescription: test',
                      'name: sample-skill\ndescription: 12'):
            with self.subTest(front=front):
                self.entry(front)
                self.assertTrue(tool.lint(self.root, structure=False)[0])

    def test_nested_links_titles_spaces_and_anchors(self):
        refs = self.root / 'references/nested'
        refs.mkdir(parents=True)
        (refs / 'guide one.md').write_text('# Guide\n\n## Usage\n\n## Usage\n', encoding='utf-8')
        self.entry(body='# Entry\n\n[guide](<references/nested/guide one.md#usage-1> "Read")\n\n[reference][ref]\n\n[ref]: references/nested/guide%20one.md#usage\n')
        self.assertEqual(([], []), tool.lint(self.root, structure=False))
        (refs / 'guide one.md').write_text('# Guide\n\n[bad](missing.md)\n', encoding='utf-8')
        errors = tool.lint(self.root, structure=False)[0]
        self.assertTrue(any('missing heading anchor' in e for e in errors))
        self.assertTrue(any('missing.md' in e for e in errors))

    def test_links_outside_skill_and_absolute_paths(self):
        (self.base / 'outside.md').write_text('# Outside', encoding='utf-8')
        for target in ('../outside.md', '/tmp/file.md', 'file:///tmp/file.md', 'C:/data/file.md'):
            with self.subTest(target=target):
                self.entry(body=f'# Entry\n\n[external]({target})\n')
                self.assertTrue(tool.lint(self.root, structure=False)[0])

    def test_reinitialization_preserves_existing_files(self):
        tool.initialize(self.root, 'C1.local')
        self.entry()
        before = {p: p.read_bytes() for p in self.root.rglob('*.md')}
        created, skipped = tool.initialize(self.root, 'C1.local')
        self.assertEqual([], created)
        self.assertEqual(9, len(skipped))
        self.assertEqual(before, {p: p.read_bytes() for p in self.root.rglob('*.md')})

    def test_invalid_categories_have_no_side_effects(self):
        for raw in ('C1.local,C1.remote', 'C1.local,C2.../escape', '../escape', 'C1.local,', 'C1./escape'):
            with self.subTest(raw=raw):
                with self.assertRaises(ValueError):
                    tool.initialize(self.root, raw)
                self.assertFalse(self.root.exists())

    def test_preflight_does_not_partially_write(self):
        self.root.mkdir()
        (self.root / 'references').write_text('existing file', encoding='utf-8')
        with self.assertRaises(ValueError):
            tool.initialize(self.root, 'C1.local')
        self.assertFalse((self.root / 'SKILL.md').exists())
        self.assertEqual(['references'], [p.name for p in self.root.iterdir()])

    def test_generic_style_accepts_questions_and_wide_table(self):
        self.entry(body='# CSV\n\n## Why validate?\n\n| A | B | C |\n| --- | --- | --- |\n| 1 | 2 | 3 |\n')
        self.assertEqual(([], []), tool.lint(self.root, structure=False))
        self.assertTrue(tool.lint(self.root, style='numbered', structure=False)[0])

    def test_code_links_not_executed_or_checked(self):
        self.entry(body='# Guide\n\n```text\n[example](missing.md)\n```\n\n`[literal](missing.md)`\n')
        self.assertEqual(([], []), tool.lint(self.root, structure=False))

    def test_template_documents_excluded_but_link_targets_checked(self):
        self.entry(body='# Entry\n\n[template](resources/templates/sample.md)\n')
        dest = self.root / 'resources/templates/sample.md'
        dest.parent.mkdir(parents=True)
        dest.write_text('# {{title}}\n\nTODO [sample](missing.md)', encoding='utf-8')
        self.assertEqual(([], []), tool.lint(self.root, structure=False))
        dest.unlink()
        self.assertTrue(tool.lint(self.root, structure=False)[0])

    def test_placeholders_ignore_code_but_catch_cjk_adjacent_todo(self):
        self.entry(body='# Guide\n\n```powershell\npython run.py --slug "<slug>" # TODO\n```\n\nSee `Cx.<slug>-workflow.md`.\n')
        self.assertEqual(([], []), tool.lint(self.root, structure=False))
        for body in ('# Guide\n\nTODO定义输入。\n', '# Guide\n\n填写 <slug> 后执行。\n'):
            with self.subTest(body=body):
                self.entry(body=body)
                self.assertTrue(any('placeholder' in e for e in tool.lint(self.root, structure=False)[0]))

    def test_name_and_description_limits(self):
        long_root = self.base / ('a' * 64)
        long_root.mkdir()
        (long_root / 'SKILL.md').write_text(f'---\nname: {"a" * 64}\ndescription: ok\n---\n\n# A\n', encoding='utf-8')
        self.assertEqual(([], []), tool.lint(long_root, structure=False))
        for front in (f'name: sample-skill\ndescription: {"x" * 1025}',
                      'name: sample-skill\ndescription: Use <tool> safely.'):
            with self.subTest(front=front[:40]):
                self.entry(front)
                self.assertTrue(tool.lint(self.root, structure=False)[0])
        for name in ('claude-helper', 'a' * 65):
            with self.subTest(name=name):
                with self.assertRaises(ValueError):
                    tool.initialize(self.base / name)
        agent_claude = self.base / 'verdantflare-agent-claude'
        tool.initialize(agent_claude)
        self.assertEqual([], tool.lint(agent_claude, style='numbered', allow_placeholders=True)[0])

    def test_sibling_skill_links_allowed_other_external_rejected(self):
        peer = self.base / 'peer-skill'
        (peer / 'scripts').mkdir(parents=True)
        (peer / 'SKILL.md').write_text('---\nname: peer-skill\ndescription: peer\n---\n\n# Peer\n\n## Usage\n', encoding='utf-8')
        (peer / 'scripts/run.py').write_text('', encoding='utf-8')
        (self.base / 'loose.md').write_text('# Loose\n', encoding='utf-8')
        self.entry(body='# Entry\n\n[peer](../peer-skill/SKILL.md#usage) [run](../peer-skill/scripts/run.py)\n')
        self.assertEqual(([], []), tool.lint(self.root, structure=False))
        for target in ('../peer-skill/missing.py', '../peer-skill/SKILL.md#nope', '../loose.md'):
            with self.subTest(target=target):
                self.entry(body=f'# Entry\n\n[x]({target})\n')
                self.assertTrue(tool.lint(self.root, structure=False)[0])

    def test_repository_asset_links_allowed_inside_repo_only(self):
        repo = self.base / 'repo'
        (repo / '.git').mkdir(parents=True)
        (repo / 'workflows').mkdir()
        (repo / 'workflows/flow.json').write_text('{}', encoding='utf-8')
        (repo / 'docs').mkdir()
        (repo / 'docs/guide.md').write_text('# Guide\n\n## Usage\n', encoding='utf-8')
        (self.base / 'outside.md').write_text('# Outside\n', encoding='utf-8')
        self.root = repo / 'skills' / 'sample-skill'
        self.root.mkdir(parents=True)
        self.entry(body='# Entry\n\n[flow](../../workflows/flow.json) [doc](../../docs/guide.md#usage)\n')
        self.assertEqual(([], []), tool.lint(self.root, structure=False))
        for target in ('../../workflows/missing.json', '../../docs/guide.md#nope', '../../../outside.md'):
            with self.subTest(target=target):
                self.entry(body=f'# Entry\n\n[x]({target})\n')
                self.assertTrue(tool.lint(self.root, structure=False)[0])

    def test_cli_from_different_working_directory(self):
        script = str(Path(tool.__file__).resolve())
        result = subprocess.run([sys.executable, '-X', 'utf8', script, 'init', str(self.root), '--categories', 'C1.local'], cwd=self.base, capture_output=True, text=True)
        self.assertEqual(0, result.returncode, result.stderr)
        result = subprocess.run([sys.executable, '-X', 'utf8', script, 'lint', str(self.root), '--allow-placeholders'], cwd=self.base, capture_output=True, text=True)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn('not delivery-ready', result.stdout)
        result = subprocess.run([sys.executable, '-X', 'utf8', script, 'lint', str(self.root)], cwd=self.base, capture_output=True, text=True)
        self.assertEqual(1, result.returncode)


if __name__ == '__main__':
    unittest.main()
