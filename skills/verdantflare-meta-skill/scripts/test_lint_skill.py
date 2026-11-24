"""Behavioral regression tests for capability-entrypoint scaffold and static validation contracts."""
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

    def write_capability(self, cap='01.convert', body=None):
        refs = self.root / 'references'
        refs.mkdir(parents=True, exist_ok=True)
        content = body or (
            f'# Sample：{cap}\n\n'
            '## 1. 输入与输出\n\n- 输入：CSV 文件\n- 输出：JSON 文件\n\n'
            '## 2. 分类\n\n标准表格输入。\n\n'
            '## 3. 工作流\n\n读取并转换。\n\n'
            '## 4. 规范\n\nUTF-8 编码。\n\n'
            '## 5. 验收\n\n合法 JSON 输出。\n'
        )
        (refs / f'{cap}.entrypoint.md').write_text(content, encoding='utf-8')

    def standard(self, sections=('1. 范围与边界', '2. 能力清单与按任务读取', '3. 工具与命令'),
                 capabilities=('01.convert',), extra=''):
        rows = ['| 原子能力 | 入口文档 |', '| :-- | :-- |']
        for cap in capabilities:
            self.write_capability(cap)
            rows.append(f'| 执行 {cap} | [{cap}.entrypoint.md](references/{cap}.entrypoint.md) |')
        heads = [f'## {title}\n\nText.\n' for title in sections]
        route_idx = 2 if len(sections) == 4 else 1
        heads[route_idx] = heads[route_idx] + '\n' + '\n'.join(rows) + '\n'
        self.entry(body='# Sample\n\n' + '\n'.join(heads) + '\n' + extra)

    def legacy_standard(self):
        refs = self.root / 'references'
        refs.mkdir(parents=True, exist_ok=True)
        rows = []
        for dim in tool.DIMENSIONS:
            (refs / f'01.base-{dim}.md').write_text(f'# {dim}\n\nDone.\n', encoding='utf-8')
            rows.append(f'- [{dim}](references/01.base-{dim}.md)')
        body = ('# Sample\n\n## 1. 范围与边界\n\nText.\n\n## 2. 按任务读取\n\n'
                + '\n'.join(rows) + '\n\n## 3. 工具与命令\n\nText.\n')
        self.entry(body=body)

    def test_default_scaffold_is_capability_layout(self):
        tool.initialize(self.root)
        names = sorted(p.relative_to(self.root).as_posix() for p in self.root.rglob('*.md'))
        self.assertEqual(['SKILL.md', 'references/01.core.entrypoint.md'], names)
        self.assertTrue(tool.lint(self.root)[0])
        errors, warnings = tool.lint(self.root, style='numbered', allow_placeholders=True)
        self.assertEqual([], errors)
        self.assertTrue(warnings)

    def test_multi_capability_and_optional_common_scaffold(self):
        root = self.base / 'vf-video'
        tool.initialize(root, capabilities_arg='01.t2v,02.i2v,03.fl2v,04.ref2v', common_arg='rules,acceptance')
        names = sorted(p.relative_to(root).as_posix() for p in root.rglob('*.md'))
        self.assertEqual([
            'SKILL.md',
            'references/00.common.acceptance.md',
            'references/00.common.rules.md',
            'references/01.t2v.entrypoint.md',
            'references/02.i2v.entrypoint.md',
            'references/03.fl2v.entrypoint.md',
            'references/04.ref2v.entrypoint.md',
        ], names)
        errors, warnings = tool.lint(root, style='numbered', allow_placeholders=True)
        self.assertEqual([], errors)
        self.assertTrue(warnings)
        for path in root.rglob('*.md'):
            self.assertNotIn('{{', path.read_text(encoding='utf-8'))

    def test_auto_numbered_and_split_capability_scaffold(self):
        root = self.base / 'vf-video'
        tool.initialize(root, capabilities_arg='t2v,i2v', common_arg='rules', split=True)
        errors, warnings = tool.lint(root, style='numbered', allow_placeholders=True)
        self.assertEqual([], errors)
        self.assertTrue(warnings)
        self.assertTrue((root / 'references/01.t2v.workflow.md').is_file())
        self.assertTrue((root / 'references/02.i2v.acceptance.md').is_file())

    def test_capability_validation_rules(self):
        self.standard(capabilities=('01.t2v', '02.i2v'))
        self.assertEqual(([], []), tool.lint(self.root, style='numbered'))

        # Missing link to entrypoint in SKILL.md
        self.write_capability('03.fl2v')
        errors = tool.lint(self.root)[0]
        self.assertTrue(any('must link capability entrypoint references/03.fl2v.entrypoint.md' in e for e in errors), errors)
        (self.root / 'references/03.fl2v.entrypoint.md').unlink()

        # Duplicate numeric prefix
        self.write_capability('01.other')
        errors = tool.lint(self.root)[0]
        self.assertTrue(any('duplicate numeric prefix 01' in e for e in errors), errors)
        (self.root / 'references/01.other.entrypoint.md').unlink()

        # Orphan dimension file
        (self.root / 'references/09.orphan.workflow.md').write_text('# Orphan\n', encoding='utf-8')
        errors = tool.lint(self.root)[0]
        self.assertTrue(any('orphan dimension file 09.orphan.workflow.md' in e for e in errors), errors)
        (self.root / 'references/09.orphan.workflow.md').unlink()

        # Unlinked dimension file for existing capability
        (self.root / 'references/01.t2v.rules.md').write_text('# Rules\n', encoding='utf-8')
        errors = tool.lint(self.root)[0]
        self.assertTrue(any('01.t2v.rules.md must be linked' in e for e in errors), errors)
        (self.root / 'references/01.t2v.rules.md').unlink()

        # Malformed common filename
        (self.root / 'references/00.common.misc.md').write_text('# Misc\n', encoding='utf-8')
        errors = tool.lint(self.root)[0]
        self.assertTrue(any('malformed filename 00.common.misc.md' in e for e in errors), errors)
        (self.root / 'references/00.common.misc.md').unlink()

        # Putting 00.common.*.md as a capability row in SKILL.md routing table is rejected
        (self.root / 'references/00.common.rules.md').write_text('# Common Rules\n', encoding='utf-8')
        text = (self.root / 'SKILL.md').read_text(encoding='utf-8')
        bad_text = text.replace(
            '| 执行 02.i2v | [02.i2v.entrypoint.md](references/02.i2v.entrypoint.md) |',
            '| 执行 02.i2v | [02.i2v.entrypoint.md](references/02.i2v.entrypoint.md) |\n'
            '| 查阅公共规范 | [00.common.rules.md](references/00.common.rules.md) |',
        )
        (self.root / 'SKILL.md').write_text(bad_text, encoding='utf-8')
        errors = tool.lint(self.root)[0]
        self.assertTrue(any('do not list 00.common.*.md' in e for e in errors), errors)
        (self.root / 'SKILL.md').write_text(text, encoding='utf-8')
        (self.root / 'references/00.common.rules.md').unlink()

        # Missing required H2 sections inside entrypoint
        self.write_capability('01.t2v', body='# T2V\n\n## 1. 分类\n\nOnly one.\n')
        errors = tool.lint(self.root)[0]
        self.assertTrue(any('missing H2 section for 输入与输出' in e for e in errors), errors)
        self.assertTrue(any('missing H2 section for 验收' in e for e in errors), errors)

    def test_crew_profile_leak_detection(self):
        crew = self.base / 'mk-crew-demo'
        self.root = crew
        self.entry(
            front='name: mk-crew-demo\ndescription: Crew demo.',
            body='# Crew\n\n## 1. 范围与边界\n\nText.\n\n## 2. 能力清单与按任务读取\n\n'
                 '| 原子能力 | 入口文档 |\n| :-- | :-- |\n'
                 '| 换脸交付 | [01.swap.entrypoint.md](references/01.swap.entrypoint.md) |\n\n'
                 '## 3. 工具与命令\n\nText.\n',
        )
        self.write_capability('01.swap')
        self.assertEqual(([], []), tool.lint(crew, style='numbered'))

        # Auto-detected crew profile catches low-level leaks
        self.write_capability(
            '01.swap',
            body='# Crew：01.swap\n\n## 1. 输入与输出\n\nIn/Out.\n\n## 2. 分类\n\nCat.\n\n'
                 '## 3. 工作流\n\nLoad model_v2.safetensors on Node 6 with LoRA weight: 0.8 and KSampler.\n\n'
                 '## 4. 规范\n\nRules.\n\n## 5. 验收\n\nAcc.\n',
        )
        errors, warnings = tool.lint(crew, style='numbered')
        self.assertEqual([], errors)
        joined = '\n'.join(warnings)
        self.assertIn('model weight filename', joined)
        self.assertIn('ComfyUI node id', joined)
        self.assertIn('LoRA weight ratio', joined)
        self.assertIn('low-level sampler parameter', joined)
        # Explicit atomic profile skips crew leak warnings
        self.assertEqual(([], []), tool.lint(crew, style='numbered', profile='atomic'))

        # Craft and director profiles auto-detect and catch leaks too
        for slug, expected in (('mk-craft-camera', 'craft profile leak'), ('mk-director-drama', 'director profile leak')):
            with self.subTest(slug=slug):
                self.root = self.base / slug
                self.entry(
                    front=f'name: {slug}\ndescription: Demo.',
                    body='# Demo\n\n## 1. 范围与边界\n\nText.\n\n## 2. 能力清单与按任务读取\n\n'
                         '| 原子能力 | 入口文档 |\n| :-- | :-- |\n'
                         '| 运镜设计 | [01.plan.entrypoint.md](references/01.plan.entrypoint.md) |\n\n'
                         '## 3. 工具与命令\n\nText.\n',
                )
                self.write_capability(
                    '01.plan',
                    body='# Demo：01.plan\n\n## 1. 输入与输出\n\nIn.\n\n## 2. 分类\n\nC.\n\n'
                         '## 3. 工作流\n\nUse base.safetensors.\n\n## 4. 规范\n\nR.\n\n## 5. 验收\n\nA.\n',
                )
                errs, warns = tool.lint(self.root, style='numbered')
                self.assertEqual([], errs)
                self.assertTrue(any(expected in w for w in warns), warns)

    def test_entry_structure_rules(self):
        self.standard(sections=('1. 编码规范', '2. 范围与边界', '3. 能力清单与按任务读取', '4. 工具与命令'))
        self.assertEqual(([], []), tool.lint(self.root))
        cases = {
            'H2 sections': dict(sections=('1. 范围与边界', '2. 能力清单与按任务读取', '3. 工具与命令', '4. 经验教训')),
            'H2 sections must': dict(sections=('1. 工具与命令', '2. 能力清单与按任务读取', '3. 范围与边界')),
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

    def test_clean_root_and_legacy_compatibility(self):
        self.legacy_standard()
        self.assertEqual(([], []), tool.lint(self.root))
        (self.root / 'FAQ.md').write_text('# FAQ\n', encoding='utf-8')
        self.assertTrue(any('FAQ.md' in e for e in tool.lint(self.root)[0]))
        (self.root / 'FAQ.md').unlink()
        (self.root / 'references/01.base-rules.md').unlink()
        self.assertTrue(any('missing 01.base-rules.md' in e for e in tool.lint(self.root)[0]))

    def test_long_reference_warns(self):
        self.standard()
        (self.root / 'references/01.convert.entrypoint.md').write_text(
            '# C\n\n## 1. 输入与输出\n\n## 2. 分类\n\n## 3. 工作流\n\n## 4. 规范\n\n## 5. 验收\n\n'
            + 'x' * tool.MAX_REFERENCE_CHARS, encoding='utf-8')
        errors, warnings = tool.lint(self.root)
        self.assertEqual([], errors)
        self.assertTrue(any('01.convert.entrypoint.md' in w for w in warnings))

    def test_legacy_generic_four_row_routing_table_warns(self):
        refs = self.root / 'references'
        refs.mkdir(parents=True, exist_ok=True)
        for dim in tool.DIMENSIONS:
            (refs / f'01.base-{dim}.md').write_text(f'# {dim}\n\n## 2. 核心流程\n\nDone.\n', encoding='utf-8')
        generic_table = (
            '| 当前任务 | 读取资料 |\n'
            '| :-- | :-- |\n'
            '| 判断是否适用、选择分支 | [分类与边界](references/01.base-category.md) |\n'
            '| 执行操作与失败恢复 | [执行流程](references/01.base-workflow.md) |\n'
            '| 核对约束、默认值与例外 | [规则约束](references/01.base-rules.md) |\n'
            '| 验证、门禁与交付报告 | [验收标准](references/01.base-acceptance.md) |\n'
        )
        self.entry(body='# Sample\n\n## 1. 范围与边界\n\nText.\n\n## 2. 按任务读取\n\n' + generic_table + '\n## 3. 工具与命令\n\nText.\n')
        errors, warnings = tool.lint(self.root)
        self.assertEqual([], errors)
        self.assertTrue(any('generic base dimensions' in w for w in warnings), warnings)

        anchored_table = generic_table.replace(
            '(references/01.base-workflow.md)',
            '(references/01.base-workflow.md#2-核心流程)',
        )
        self.entry(body='# Sample\n\n## 1. 范围与边界\n\nText.\n\n## 2. 能力清单与按任务读取\n\n' + anchored_table + '\n## 3. 工具与命令\n\nText.\n')
        self.assertEqual(([], []), tool.lint(self.root))

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
        tool.initialize(self.root, '01.t2v', 'rules')
        self.entry()
        before = {p: p.read_bytes() for p in self.root.rglob('*.md')}
        created, skipped = tool.initialize(self.root, '01.t2v', 'rules')
        self.assertEqual([], created)
        self.assertEqual(3, len(skipped))
        self.assertEqual(before, {p: p.read_bytes() for p in self.root.rglob('*.md')})

    def test_invalid_capabilities_and_common_have_no_side_effects(self):
        for caps, common in (('00.t2v', ''), ('01.t2v,01.i2v', ''), ('01.t2v,02.t2v', ''),
                             ('01.../escape', ''), ('01.t2v,', ''), ('01.t2v', 'unknown')):
            with self.subTest(caps=caps, common=common):
                with self.assertRaises(ValueError):
                    tool.initialize(self.root, caps, common)
                self.assertFalse(self.root.exists())

    def test_preflight_does_not_partially_write(self):
        self.root.mkdir()
        (self.root / 'references').write_text('existing file', encoding='utf-8')
        with self.assertRaises(ValueError):
            tool.initialize(self.root, '01.t2v')
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
        self.entry(body='# Guide\n\n```powershell\npython run.py --slug "<slug>" # TODO\n```\n\nSee `01.<capability>.entrypoint.md`.\n')
        self.assertEqual(([], []), tool.lint(self.root, structure=False))
        for body in ('# Guide\n\nTODO定义输入。\n', '# Guide\n\n填写 <capability> 后执行。\n'):
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
        result = subprocess.run(
            [sys.executable, '-X', 'utf8', script, 'init', str(self.root), '--capabilities', '01.t2v,02.i2v', '--common', 'rules'],
            cwd=self.base, capture_output=True, text=True)
        self.assertEqual(0, result.returncode, result.stderr)
        result = subprocess.run(
            [sys.executable, '-X', 'utf8', script, 'lint', str(self.root), '--allow-placeholders'],
            cwd=self.base, capture_output=True, text=True)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn('not delivery-ready', result.stdout)
        result = subprocess.run(
            [sys.executable, '-X', 'utf8', script, 'lint', str(self.root)],
            cwd=self.base, capture_output=True, text=True)
        self.assertEqual(1, result.returncode)


if __name__ == '__main__':
    unittest.main()
