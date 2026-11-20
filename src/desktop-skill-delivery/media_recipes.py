"""Explicit Image/MV public-document projections, separate from candidate promotion."""
from __future__ import annotations

IMAGE = 'verdantflare-image'
MV = 'verdantflare-music-mv'
COMMON = ('references/common-connection.md', 'references/common-project-world.md')
FILES = {
    IMAGE: ('SKILL.md', 'agents/openai.yaml', 'scripts/image_client.py', 'scripts/load_env.py',
            'references/cli.md', 'references/contracts.md', 'references/workflow.md',
            'references/visual-specs.md', 'references/google.md', 'references/grok.md',
            'references/moss-image.md', 'references/content-rating-step.md',
            'references/content-rating-rubric.md', *COMMON),
    MV: ('SKILL.md', 'agents/openai.yaml', 'scripts/load_env.py',
         'references/character-assets.md', 'references/contracts.md', 'references/workflow.md',
         'references/review-gates.md', 'references/content-rating-step.md',
         'references/content-rating-rubric.md', *COMMON),
}
PIN = 'https://github.com/verdantflarehub/verdantflare-skills/blob/d52d2ad317ab8cd220c03682b1a88b212b995d45/skills/'
# Explicit source revision migration: the destination is locked within the release,
# never fetched as a floating latest. See integration design before promotion.
REPLACEMENTS = {
    (IMAGE, 'SKILL.md'): (
        ('../_shared/project-world.md', 'references/common-project-world.md', 1),
        ('../ENVIRONMENT.md', 'references/common-connection.md', 2),
        ('World / Character 形象基线（`docs/design/world/character/identity.md`）',
         'World / Character 形象基线及当前工具 Schema', 1),
    ),
    (IMAGE, 'references/cli.md'): (
        ('../../_shared/project-world-client.md', '../../verdantflare-project/references/workspace.md', 1),
        ('../../ENVIRONMENT.md', 'common-connection.md', 1),
    ),
    (IMAGE, 'references/contracts.md'): (
        (PIN + 'ENVIRONMENT.md', 'common-connection.md', 1),
        ('（历史记录见 `docs/archive/bugs/bug_20261111_image_chatgpt_google_2k_resolution_mismatch.md`）', '', 1),
    ),
    (IMAGE, 'references/visual-specs.md'): (
        ('服务端修复 `docs/archive/bugs/bug_20261111_image_chatgpt_google_2k_resolution_mismatch.md` 后，',
         '按当前服务端各渠道实际输出行为，', 1),
    ),
    (IMAGE, 'references/google.md'): (
        ('；修复记录见 `docs/archive/bugs/bug_20261111_image_chatgpt_google_2k_resolution_mismatch.md`', '', 1),
    ),
    (IMAGE, 'references/moss-image.md'): (
        ('- MOSS-Image 主设计（`docs/design/app/image/moss-image.md`）：已批准的一期能力与参考图限制。\n', '', 1),
        ('- 多参考图参数契约（`docs/design/app/image/moss-image.md#167-多参考图参数契约待-h1`）：新模式的唯一设计依据，尚未批准或交付，不作为当前调用能力。\n', '', 1),
        ('（对应主设计 `docs/design/app/image/moss-image.md` 第 7 节版式 JSON 校验）', '', 1),
        ('已公开的无人场景latent编辑见第3.4节', '已公开的无人场景latent编辑见第3.3节', 1),
        ('### 3.4 无人场景 latent 编辑（按实时能力启用）', '### 3.3 无人场景 latent 编辑（按实时能力启用）', 1),
        ('该限域路径与第3.3节的人物／服装组合模式分开。', '该限域路径不包含未公开的人物／服装组合模式。', 1),
        ('- **按需启停与租约**：`moss-image` 使用按需启停租约 `gpu-task-moss-image`（空闲 10 分钟缩回 0，等待 Music3 空闲）。',
         '- **冷启动与排队**：计算资源由 VF 后台维护。', 1),
    ),
    (IMAGE, 'references/workflow.md'): (
        (PIN + 'ENVIRONMENT.md', 'common-connection.md', 1),
        (PIN + 'verdantflare-project/SKILL.md', '../../verdantflare-project/SKILL.md', 1),
        (PIN + 'verdantflare-project/references/revisions.md#5-创作中的素材增删改查',
         '../../verdantflare-project/references/revisions.md#5-创作中的素材增删改查', 1),
    ),
    (MV, 'SKILL.md'): (
        ('../_shared/project-world.md', 'references/common-project-world.md', 1),
        ('../ENVIRONMENT.md', 'references/common-connection.md', 1),
    ),
    (MV, 'references/workflow.md'): (
        (PIN + 'verdantflare-video/references/prompting.md#跨镜连续性检查',
         '../../verdantflare-video/references/prompting.md#跨镜连续性检查', 1),
    ),
}


def source_key(pkg, name):
    if pkg not in FILES or name not in FILES[pkg]:
        raise ValueError('PUBLIC_FILE_NOT_ALLOWED')
    if name in COMMON:
        return 'src/desktop-skill-delivery/public-docs/' + name.split('/')[-1]
    return f'skills/{pkg}/{name}'


def transform_name(pkg, name):
    return 'media-public-guide-v1' if (pkg, name) in REPLACEMENTS else 'copy'


def transform(pkg, name, raw):
    source_key(pkg, name)
    changes = REPLACEMENTS.get((pkg, name))
    if not changes:
        return raw
    text = raw.decode('utf-8')
    if (pkg, name) == (IMAGE, 'references/moss-image.md'):
        start = '### 3.3 多参考图模式摘要（待 H1，不是当前调用能力）\n'
        end = '### 3.4 无人场景 latent 编辑（按实时能力启用）\n'
        if text.count(start) != 1 or text.count(end) != 1 or text.index(start) >= text.index(end):
            raise ValueError('PUBLIC_DOCUMENT_SOURCE_SHAPE_CHANGED')
        text = text[:text.index(start)] + text[text.index(end):]
        before = '- **待审组合模式的失败**：全部阶段成功才交付最终图；换脸失败时保留中间图与拒绝原因，但中间图不能冒充完整结果。内容或姿态拒绝不得通过改检查阈值、跳过换脸或换云渠道绕过；结果未知时核对原阶段记录，不自动重新生成。具体阶段恢复按主设计及发布后的实际能力执行。\n'
        if text.count(before) != 1:
            raise ValueError('PUBLIC_DOCUMENT_SOURCE_SHAPE_CHANGED')
        text = text.replace(before, '')
    for before, after, count in changes:
        if text.count(before) != count:
            raise ValueError('PUBLIC_DOCUMENT_SOURCE_SHAPE_CHANGED')
        text = text.replace(before, after)
    return text.encode('utf-8')
