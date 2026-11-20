"""Public Music file recipes; aliases preserve original Unicode source identities."""
from __future__ import annotations

import hashlib

MUSIC = 'verdantflare-music'
ALIASES = {
    'global-review.md': '全局-审核规范.md',
    'lyrics-general.md': '歌词-审核规范.md',
    'lyrics-city-pop-funk.md': '歌词-City Pop与Funk-审核规范.md',
    'lyrics-rnb.md': '歌词-R&B-审核规范.md',
    'lyrics-mandopop-rock.md': '歌词-华语流行摇滚-审核规范.md',
    'lyrics-punk-metal.md': '歌词-朋克与金属-审核规范.md',
    'lyrics-indie-dream-pop.md': '歌词-独立流行与Dream Pop-审核规范.md',
    'lyrics-jpop-electronic.md': '歌词-现代日系电子流行-审核规范.md',
    'lyrics-cinematic-pop.md': '歌词-电影感流行-审核规范.md',
    'lyrics-rap.md': '歌词-说唱-审核规范.md',
    'arrangement-general.md': '编曲-审核规范.md',
    'arrangement-city-pop-funk.md': '编曲-City Pop与Funk-审核规范.md',
    'arrangement-rnb.md': '编曲-R&B-审核规范.md',
    'arrangement-country.md': '编曲-乡村音乐-审核规范.md',
    'arrangement-mandopop-rock.md': '编曲-华语流行摇滚-审核规范.md',
    'arrangement-duet-ensemble.md': '编曲-对唱与合唱-审核规范.md',
    'arrangement-anime-rock.md': '编曲-日式二次元燃系-审核规范.md',
    'arrangement-punk-metal.md': '编曲-朋克与金属-审核规范.md',
    'arrangement-indie-dream-pop.md': '编曲-独立流行与Dream Pop-审核规范.md',
    'arrangement-jpop-electronic.md': '编曲-现代日系电子流行-审核规范.md',
    'arrangement-electronic.md': '编曲-现代电子-审核规范.md',
    'arrangement-cinematic-pop.md': '编曲-电影感流行-审核规范.md',
    'arrangement-britpop.md': '编曲-英式摇滚-审核规范.md',
    'arrangement-rap.md': '编曲-说唱-审核规范.md',
}
BASE_FILES = (
    'SKILL.md', 'agents/openai.yaml',
    'references/artifacts.md', 'references/content-rating-rubric.md', 'references/content-rating-step.md',
    'references/duet-reference-water-crystal.md', 'references/jpop-electronic-production.md',
    'references/local-redraw.md', 'references/multi-vocal.md', 'references/one-click-cover.md',
    'references/one-click-duet.md', 'references/one-click-ensemble.md',
    'references/one-click-independent-generation.md', 'references/one-click-score-constrained-cover.md',
    'references/one-click-score-extraction.md', 'references/one-click-voice-replacement.md',
    'references/picture-scoring.md', 'references/preflight.md', 'references/review-gates.md',
    'references/score-plan-checks.md', 'references/vocal-replacement-mix.md',
    'references/voice-preparation.md', 'references/workflow.md',
    'scripts/analyze_tail.py', 'scripts/audit_performance_voice_source.py', 'scripts/lift_duet_singer_mask.py',
    'scripts/prepare_duet_replacement_backing.py',
    'scripts/replace_performance_duet_voice.py', 'scripts/replace_singer_in_mix.py',
    'scripts/validate_duet.py', 'scripts/validate_performance_duet.py', 'scripts/validate_score_plan.py',
)
COMMON = ('references/common-connection.md', 'references/common-project-world.md')
SNAPSHOTS = ('references/voice-model.md', 'references/voice-model.schema.json', 'references/source-provenance.json')
FILES = BASE_FILES + tuple('references/' + n for n in ALIASES) + COMMON + SNAPSHOTS
PINNED = {
    'references/voice-model.md': 'c170c8b4e98602f885ac3c39b894369fbde62a0009d29ac30c442b228e67d305',
    'references/voice-model.schema.json': '21a85dd11ccd9c1a18a5dd13bff9aa21ec7d29e6b964b8d02eb3d40932387b65',
}
ENV_URL = 'https://github.com/verdantflarehub/verdantflare-skills/blob/verdantflare-music-v0.4.1/skills/ENVIRONMENT.md'
PROJECT_URL = 'https://github.com/verdantflarehub/verdantflare-skills/blob/verdantflare-music-v0.4.1/skills/_shared/project-world.md'
VOICE_BASE = 'https://github.com/verdantflarehub/verdantflare-design/blob/cce3dc47f0954bbce9c83ebc9c4a3a1aba81697f/docs/design/'


def source_key(name):
    if name not in FILES:
        raise ValueError('PUBLIC_FILE_NOT_ALLOWED')
    if name in COMMON:
        return 'src/desktop-skill-delivery/public-docs/' + name.split('/')[-1]
    if name in SNAPSHOTS:
        return 'src/desktop-skill-delivery/public-docs/music-snapshots/' + name.split('/')[-1]
    leaf = name.split('/')[-1]
    if name.startswith('references/') and leaf in ALIASES:
        name = 'references/' + ALIASES[leaf]
    return f'skills/{MUSIC}/{name}'


def transform_name(name):
    if name in COMMON:
        return 'copy'
    return 'music-public-guide-v1' if name.endswith('.md') else 'copy'


def once(text, before, after):
    if text.count(before) != 1:
        raise ValueError('PUBLIC_DOCUMENT_SOURCE_SHAPE_CHANGED')
    return text.replace(before, after)


def transform(name, raw):
    source_key(name)
    if name in PINNED and hashlib.sha256(raw).hexdigest() != PINNED[name]:
        raise ValueError('PINNED_SNAPSHOT_CHANGED')
    if transform_name(name) == 'copy':
        return raw
    text = raw.decode('utf-8')
    # Only complete, known reference filenames are renamed, including the
    # existing percent-encoded space spelling. Script and project names stay.
    for target, source in ALIASES.items():
        text = text.replace(source, target).replace(source.replace(' ', '%20'), target)
    if name == 'SKILL.md':
        text = once(text, '，也用于已有音乐项目续作与技能研发优化。', '，也用于已有音乐项目续作。')
        text = once(text, PROJECT_URL, 'references/common-project-world.md')
        text = once(text, ENV_URL, 'references/common-connection.md')
        text = once(text, VOICE_BASE + 'world/music/voice-model.md', 'references/voice-model.md')
        paragraph = '用户要求研发或优化本技能时，处理通用工作流与能力契约，不以某首歌作为默认交付，也不自动运行音乐生成、转换或远端部署。先核对技能说明、本地服务实现、实际部署工具三者的差异；只把真实可用的能力写成可执行步骤。按 [技能研发检查](references/skill-development.md) 维护配置、文档与验证边界。\n\n'
        text = once(text, paragraph, '')
    elif name == 'references/artifacts.md':
        text = once(text, VOICE_BASE + 'contracts/v2/voice-model.schema.json', 'voice-model.schema.json')
    elif name == 'references/voice-model.md':
        text = once(text, '> 归属：[World / Music](README.md)；业务发布与授权见 [World 发布设计](../../studio/details/05-studio-world-design.asset-publication.md)。',
                    '> 本页为固定领域设计的公开快照，文件格式见 [VoiceModel Schema](voice-model.schema.json)，来源与摘要见 [快照来源](source-provenance.json)。实际操作按同一发行集合的 Project／Artifact 技能及本次 Studio 工具 Schema 执行。')
        footer = '依据：[Music 文件约定](../../../../verdantflare-skills/skills/verdantflare-music/references/artifacts.md)、[Music 流程](../../../../verdantflare-skills/skills/verdantflare-music/references/workflow.md)、[声音准备](../../../../verdantflare-skills/skills/verdantflare-music/references/voice-preparation.md)。这些资料用于设计兼容，本轮不执行媒体生产。'
        text = once(text, footer, '使用说明：[Music 文件约定](artifacts.md)、[Music 流程](workflow.md)、[声音准备](voice-preparation.md)。模型格式声明与真实可加载性分别核验。')
    return text.encode('utf-8')
