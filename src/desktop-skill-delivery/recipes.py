"""Explicit public Video projections; originals remain in the source repository."""
from __future__ import annotations

import hashlib
import json

from video_projection import project as project_video
import media_recipes
import music_recipes
import speech_recipes
import drama_recipes
import blender_recipes

VIDEO = 'verdantflare-video'
COMMON = ('common-connection.md', 'common-project-world.md')
VIDEO_FILES = (
    'SKILL.md', 'agents/openai.yaml', 'scripts/video_client.py',
    'scripts/channel_contract.py', 'scripts/load_env.py', 'scripts/validate_h3_prompt.py',
    'references/workflow.md', 'references/validation.md', 'references/video-mcp.md',
    'references/content-rating-rubric.md', 'references/content-rating-step.md',
    'references/sd2-workflow.md', 'references/input-modes.md', 'references/control-evidence.md',
    'references/prompting.md', 'references/shot-acceptance.md', 'references/google.md',
    'references/grok.md', 'references/higgsfield.md', 'references/higgsfield-LICENSE.txt',
    'references/common-connection.md', 'references/common-project-world.md',
)
ENV_URL = 'https://github.com/verdantflarehub/verdantflare-skills/blob/verdantflare-video-v0.4.0/skills/ENVIRONMENT.md'
PROJECT_URL = 'https://github.com/verdantflarehub/verdantflare-skills/blob/verdantflare-video-v0.4.1/skills/_shared/project-world.md'


def source_key(pkg, name):
    if name == 'skill.json':
        return f'skills/{pkg}/SKILL.md'
    if pkg == blender_recipes.BLENDER:
        return blender_recipes.source_key(name)
    if pkg == drama_recipes.DRAMA:
        return drama_recipes.source_key(name)
    if pkg == speech_recipes.SPEECH:
        return speech_recipes.source_key(name)
    if pkg == music_recipes.MUSIC:
        return music_recipes.source_key(name)
    if pkg in media_recipes.FILES:
        return media_recipes.source_key(pkg, name)
    if pkg == VIDEO and name in {'references/' + n for n in COMMON}:
        return 'src/desktop-skill-delivery/public-docs/' + name.split('/')[-1]
    return f'skills/{pkg}/{name}'


def transform_name(pkg, name):
    if name == 'skill.json':
        return 'skill-version-descriptor-v1'
    if pkg == blender_recipes.BLENDER:
        return blender_recipes.transform_name(name)
    if pkg == drama_recipes.DRAMA:
        return drama_recipes.transform_name(name)
    if pkg == speech_recipes.SPEECH:
        return speech_recipes.transform_name(name)
    if pkg == music_recipes.MUSIC:
        return music_recipes.transform_name(name)
    if pkg in media_recipes.FILES:
        return media_recipes.transform_name(pkg, name)
    if pkg != VIDEO:
        return 'copy'
    if name == 'scripts/video_client.py':
        return 'video-mcp-ast-v1'
    if name in {'SKILL.md', 'references/higgsfield.md', 'references/sd2-workflow.md',
                'references/workflow.md', 'references/video-mcp.md',
                'references/validation.md', 'references/control-evidence.md'}:
        return 'video-public-guide-v1'
    return 'copy'


def replace_once(text, before, after):
    if text.count(before) != 1:
        raise ValueError('PUBLIC_DOCUMENT_SOURCE_SHAPE_CHANGED')
    return text.replace(before, after)


def transform(pkg, name, raw, *, release_id=None):
    if name == 'skill.json':
        if not isinstance(release_id, str) or not release_id:
            raise ValueError('MISSING_DESCRIPTOR_RELEASE')
        return (json.dumps({'name': pkg, 'version': release_id},
                           ensure_ascii=False, sort_keys=True, indent=2) + '\n').encode('utf-8')
    if pkg == blender_recipes.BLENDER:
        return blender_recipes.transform(name, raw)
    if pkg == drama_recipes.DRAMA:
        return drama_recipes.transform(name, raw)
    if pkg == speech_recipes.SPEECH:
        return speech_recipes.transform(name, raw)
    if pkg == music_recipes.MUSIC:
        return music_recipes.transform(name, raw)
    if pkg in media_recipes.FILES:
        return media_recipes.transform(pkg, name, raw)
    kind = transform_name(pkg, name)
    if kind == 'copy':
        return raw
    if kind == 'video-mcp-ast-v1':
        return project_video(raw, hashlib.sha256(raw).hexdigest())[0]
    text = raw.decode('utf-8')
    if name == 'SKILL.md':
        text = replace_once(text, PROJECT_URL, 'references/common-project-world.md')
        text = replace_once(text, ENV_URL, 'references/common-connection.md')
        text = replace_once(text,
            '官方 CLI 封装在本 Skill 私有运行时，仅供管理员授权维护；成员生成、查询及结果通过 Studio，会员凭据留在服务器。',
            '会员渠道授权由 VF 后台维护；生成、查询及结果通过 Studio，会员凭据留在服务器。')
        text = replace_once(text,
            'H3-Sing 支持双档加速与全链路后置交付：`quality_profile` 支持 `fasth3-nvfp4`（5090 默认 8-NFE QAD 加速档）与 `du-0`（4-NFE 回退档），CLI 通过 `--quality-profile` 指定；',
            'H3-Sing 使用 FastH3 单档加速与全链路后置交付：`quality_profile` 仅支持 `fasth3-nvfp4`（8-NFE QAD 加速档），CLI 通过 `--quality-profile` 指定；')
    elif name == 'references/higgsfield.md':
        start, end = '## 3. 管理员私有 CLI\n', '## 4. 验证边界\n'
        if text.count(start) != 1 or text.count(end) != 1 or text.index(start) >= text.index(end):
            raise ValueError('PUBLIC_DOCUMENT_SOURCE_SHAPE_CHANGED')
        text = text[:text.index(start)] + text[text.index(end):]
        text = replace_once(text, '## 4. 验证边界', '## 3. 验证边界')
        text = replace_once(text, '## 5. 任务 Title', '## 4. 任务 Title')
        text = replace_once(text,
            '官方 CLI 固定为 1.1.26，运行时归本 Skill 的 `.runtime/` 私有目录，不要求全局安装。',
            '供应商运行时及渠道授权由 VF 后台维护，用户不安装供应商 CLI。')
        text = replace_once(text, '在这里改为私有运行时、Studio 异步任务和中央 Artifact 固定 ContentRef。',
                            '在这里改为服务端渠道调用、Studio 异步任务和中央 Artifact 固定 ContentRef。')
        text = replace_once(text,
            '本 Skill 的流程适配、私有 CLI 安装与真实宿主插件发现分别验收。官方 Skills 插件完整快照在临时研究中已安装；不能因为本 reference 存在就宣称宿主插件加载或团队调用已经通过。',
            '公开流程、宿主技能加载、MCP 连接与真实生成分别验证。此说明及官方来源链接不证明目标桌面已经支持；执行仍以实际工具发现和结果回读为准。')
    elif name == 'references/sd2-workflow.md':
        start, end = '## 2. 附带旧客户端的含义\n', 'CLI `generate` 按宿主真实能力和创建 Schema 走 Studio。'
        if text.count(start) != 1 or text.count(end) != 1 or text.index(start) >= text.index(end):
            raise ValueError('PUBLIC_DOCUMENT_SOURCE_SHAPE_CHANGED')
        text = text[:text.index(start)] + '## 2. 当前客户端与历史记录\n\n公共客户端只使用 Studio MCP。历史公共 API／S3 维护由原系统负责；没有当前可用恢复适配时保留原请求与任务记录，不让用户安装旧凭据或重复生成。\n\n' + text[text.index(end):]
    elif name == 'references/workflow.md':
        text = replace_once(text, ENV_URL, 'common-connection.md')
        text = replace_once(text, '--quality-profile {fasth3-nvfp4,du-0}', '--quality-profile fasth3-nvfp4')
        text = replace_once(text,
            '运行前确认解释器为 Python 3.10 或更新版本；系统 `python3` 低于此版本时显式使用已安装的受支持解释器（如 `python3.10`），不要改系统默认或因下载失败重新生成。源码、安装包和实际调用均使用同一受支持版本验证。',
            '需要脚本时使用发行包提供的受管 Python 3.10+ 运行时；缺少对应运行时的工程候选不能声称脚本已经可用，也不要求用户改系统 Python。可用的宿主 MCP 工具仍优先直接调用；下载失败恢复原任务，不重新生成。')
        text = replace_once(text, '按路由或任务 ID 识别 MCP／旧 SD2 记录，不能改变原渠道；',
                            '使用原 Video MCP 任务 ID 恢复，不能改变原渠道；')
        text = replace_once(text, '- `list` / `recover`：历史 SD2 查询恢复入口。\n', '')
    elif name == 'references/video-mcp.md':
        text = replace_once(text, ENV_URL, 'common-connection.md')
        text = replace_once(text,
            '`route=h3-singularity` 支持 `quality_profile` 选择 `fasth3-nvfp4`（RTX 5090 默认 8-NFE QAD 主加速档）或 `du-0`（4-NFE 回退档）；',
            '`route=h3-singularity` 的公开 `quality_profile` 仅支持 `fasth3-nvfp4`（FastH3 8-NFE QAD 加速档）；')
    elif name == 'references/validation.md':
        text = replace_once(text, 'Singularity du-0 接受至少一种参考', '当前本地 H3 路线接受至少一种参考')
    elif name == 'references/control-evidence.md':
        text = replace_once(text,
            '当前领域 MCP 只开放 fal 的 Reference-to-Video；fal 另行公开的 T2V、首帧/首尾帧、Prompt Expansion 和 LoRA 能力仍不属于已批准契约。',
            '只使用当前 Studio 实际声明的渠道、模型和输入模式；供应商另行公开的接口、Prompt Expansion 或 LoRA 能力不会自动成为本次可用能力。')
    return text.encode('utf-8')
