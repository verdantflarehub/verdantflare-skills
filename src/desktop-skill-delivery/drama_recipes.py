"""Explicit short-drama public recipes with release-locked sibling dependencies."""
from __future__ import annotations

import hashlib

DRAMA = 'x-verdantflare-short-drama'
FILES = ('SKILL.md',
 'agents/openai.yaml',
 'references/commercial/guide.md',
 'references/commercial/workflow.md',
 'references/content-rating-rubric.md',
 'references/content-rating-step.md',
 'references/directing/advertising.md',
 'references/directing/guide.md',
 'references/directing/plan-contract.md',
 'references/directing/storyboard.md',
 'references/local-breakdown/guide.md',
 'references/local-breakdown/handoff.md',
 'references/migration.md',
 'references/performance/dialogue.md',
 'references/performance/guide.md',
 'references/performance/microexpressions.md',
 'references/performance/performance.md',
 'references/performance/rework-evidence.md',
 'references/production/asset-bible-template.md',
 'references/production/blender-whitebox.md',
 'references/production/character-consistency-package.md',
 'references/production/execution-plan-template.md',
 'references/production/execution-planning.md',
 'references/production/generation-preparation.schema.json',
 'references/production/generation-review.md',
 'references/production/guide.md',
 'references/production/scene-keyframe-video.md',
 'references/production/series-workflow-template.md',
 'references/production/workflow.md',
 'references/review/guide.md',
 'references/review/script-review-standard.md',
 'references/studio-sync.md',
 'references/writing/delivery-templates.md',
 'references/writing/guide.md',
 'references/writing/script-package-standard.md',
 'scripts/breakdown.py',
 'scripts/build_identity_comparison.py',
 'scripts/validate_plan.py',
 'scripts/validate_preparation.py',
 'scripts/validate_reference_reuse.py',
 'scripts/validate_visual_review.py')
REPLACEMENTS = {'SKILL.md': [('编剧、审核和文字设计可独立使用本包。运行校验器需要 Python 3.10 或更高版本；本机拆解另外需要 Ollama 及已安装的 '
               '`gemma3:12b`，不自动拉取。图像、视频、音乐和对白执行按实际任务使用对应已安装 Skill 与宿主能力；模块中的外部链接固定到可追溯源码版本，作为依赖说明，不是自动安装或调用授权。',
               '编剧、审核和文字设计可独立使用本包。脚本使用发行集合提供的受管 Python 3.10+；准备校验需要 jsonschema，身份对照图需要 '
               'Pillow，缺运行时的工程候选不能声称这些脚本可用。本机拆解另依赖已运行的本机 Ollama 和已安装的 '
               '`gemma3:12b`，不自动下载权重或改变远端端点。媒体、项目及白模流程使用同一发行集合锁定的 Image、Video、Music、Speech、Music-MV、Project、Artifact 与 '
               '[Blender](../verdantflare-blender/SKILL.md) 技能；依赖缺失时对应步骤保持未完成，不从私有仓库临时补齐，也不由文件装配推断可调用。')],
 'references/commercial/guide.md': [('https://github.com/verdantflarehub/verdantflare-skills/blob/3c5a282fc4e8c30bda3bbfeef173f43d543bf93d/skills/verdantflare-image/SKILL.md',
                                     '../../../verdantflare-image/SKILL.md'),
                                    ('https://github.com/verdantflarehub/verdantflare-skills/blob/9320ed7f1af6dfe6dc3ad5d43cff4e68685df586/skills/verdantflare-video/SKILL.md',
                                     '../../../verdantflare-video/SKILL.md')],
 'references/migration.md': [('导演校验器和本机拆解脚本迁到包根 `scripts/`；测试迁到包根 `tests/`。',
                              '导演校验器和本机拆解脚本位于包根 `scripts/`；开发测试保留在源码侧，不进入用户包。'),
                             ('本仓库现行目录与包测试使用新入口。', '本发行集合使用新入口。')],
 'references/performance/dialogue.md': [('有已安装的 Speech 技能时读取该入口；独立安装本短剧包时，可按需读取[固定 Speech '
                                         '技能源码](https://github.com/verdantflarehub/verdantflare-skills/blob/edd08111ffe9722ea42018fcd02ae6b628667257/skills/verdantflare-speech/SKILL.md)。不依赖本地兄弟目录，也不自动安装或索取内部服务凭据。',
                                         '读取同一发行集合中锁定的 [Speech '
                                         '技能](../../../verdantflare-speech/SKILL.md)。依赖包缺失或不兼容时保留对白交接与缺口，不访问私有源码补齐、不自动安装或索取内部服务凭据。')],
 'references/performance/guide.md': [('https://github.com/verdantflarehub/verdantflare-skills/blob/3c5a282fc4e8c30bda3bbfeef173f43d543bf93d/skills/verdantflare-image/SKILL.md',
                                      '../../../verdantflare-image/SKILL.md'),
                                     ('https://github.com/verdantflarehub/verdantflare-skills/blob/9320ed7f1af6dfe6dc3ad5d43cff4e68685df586/skills/verdantflare-video/SKILL.md',
                                      '../../../verdantflare-video/SKILL.md')],
 'references/production/blender-whitebox.md': [('当短剧或宣传片场景需要在 Studio 网页片场查看器（`viewer`）中进行三维环绕、第一人称漫游与实拍机位校对时，在完成 '
                                                '`.blend` 场景与多机位预演后追加片场包导出与校验步骤：',
                                                '当短剧或宣传片场景需要在 Studio 网页片场查看器（`viewer`）中进行三维环绕、第一人称漫游与实拍机位校对时，在完成 '
                                                '`.blend` 场景与多机位预演后追加片场包导出与校验步骤：\n'
                                                '\n'
                                                '此步骤依赖同一发行集合的 [Blender '
                                                '工程归档](../../../verdantflare-blender/references/project-save.md) '
                                                '及其受控导出／校验工具。本短剧包不包含下述片场工具；Blender '
                                                '依赖尚未交付时整段导出与验证保持未完成，不能拿案例脚本、占位文件或数学检查替代真实工程与网页对齐验收。')],
 'references/production/generation-review.md': [('https://github.com/verdantflarehub/verdantflare-skills/blob/e51e12f52023bbca00c706ef818efca6d7faba9d/skills/verdantflare-project/references/mcp-authority.md',
                                                 '../../../verdantflare-project/references/mcp-authority.md')],
 'references/production/guide.md': [('https://github.com/verdantflarehub/verdantflare-skills/blob/48ce44a2513c8d1556eafec85f9a42ce80d3c9a9/skills/verdantflare-image/SKILL.md',
                                     '../../../verdantflare-image/SKILL.md'),
                                    ('https://github.com/verdantflarehub/verdantflare-skills/blob/9320ed7f1af6dfe6dc3ad5d43cff4e68685df586/skills/verdantflare-video/SKILL.md',
                                     '../../../verdantflare-video/SKILL.md'),
                                    ('https://github.com/verdantflarehub/verdantflare-skills/blob/3c5a282fc4e8c30bda3bbfeef173f43d543bf93d/skills/verdantflare-music-mv/SKILL.md',
                                     '../../../verdantflare-music-mv/SKILL.md')]}

def source_key(name):
    if name not in FILES:
        raise ValueError('PUBLIC_FILE_NOT_ALLOWED')
    return f'skills/{DRAMA}/{name}'


def transform_name(name):
    source_key(name)
    return 'short-drama-public-guide-v1' if name in REPLACEMENTS else 'copy'


def transform(name, raw):
    if transform_name(name) == 'copy':
        return raw
    text = raw.decode('utf-8')
    if name == 'references/production/blender-whitebox.md':
        start, end = '### 2.3 片场包（Set Package v1.0）导出与机位对齐校验\n', '### 2.4 人物尺度与调度证据\n'
        if text.count(start) != 1 or text.count(end) != 1 or text.index(start) >= text.index(end):
            raise ValueError('PUBLIC_DOCUMENT_SOURCE_SHAPE_CHANGED')
        if hashlib.sha256(text[text.index(start):text.index(end)].encode()).hexdigest() != '1b858528d1d07983e2a4e8c1842c09505d4cf9be597ab2c3168518d5dd2fc71b':
            raise ValueError('PUBLIC_DOCUMENT_SOURCE_SHAPE_CHANGED')
        replacement = start + '\n需要网页片场查看器交付时，按同一发行集合的 [Blender 固定工程导出与机位核验](../../../verdantflare-blender/references/set-export.md)执行。源工程、脚本与输出分别固定修订，通过实例 MCP 导出、归档并回读；不要调用团队仓库案例脚本。\n\n包结构校验、真实网页／Blender 画面核验及原生来源验证分别留证；Blender 包、运行依赖或实际能力缺失时对应步骤保持未完成。数值阈值通过不等于真实机位或桌面验收，不将技术通过记为创作选版。\n\n'
        return (text[:text.index(start)] + replacement + text[text.index(end):]).encode('utf-8')
    for before, after in REPLACEMENTS[name]:
        if text.count(before) != 1:
            raise ValueError('PUBLIC_DOCUMENT_SOURCE_SHAPE_CHANGED')
        text = text.replace(before, after)
    if name == 'references/migration.md':
        boundary = '## 3. 维护说明\n'
        if text.count(boundary) != 1:
            raise ValueError('PUBLIC_DOCUMENT_SOURCE_SHAPE_CHANGED')
        text = text[:text.index(boundary)].rstrip() + '\n'
    return text.encode('utf-8')
