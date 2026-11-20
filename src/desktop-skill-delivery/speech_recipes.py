"""Public Speech projection; developer-only morph execution stays server-side."""
from __future__ import annotations

SPEECH = 'verdantflare-speech'
FILES = (
    'SKILL.md', 'agents/openai.yaml', 'scripts/prepare_voice_profile.py', 'scripts/derive_voice_reference.py',
    'references/content-rating-step.md', 'references/content-rating-rubric.md',
    'references/new-voice.md', 'references/narration-direction.md',
    'references/voice-morph.md', 'references/voice-design.md',
    'references/voice-reference.md', 'references/tasks-and-delivery.md',
    'references/common-connection.md', 'references/common-project-world.md',
)
BASE = 'https://github.com/verdantflarehub/verdantflare-skills/blob/e51e12f52023bbca00c706ef818efca6d7faba9d/skills/'
REPLACEMENTS = {
    'SKILL.md': (
        ('用户入口统一为宿主 Studio MCP，读取 `STUDIO_MCP_URL` 与 `STUDIO_MCP_BEARER_TOKEN`，从实际工具目录确认本次需要的能力和 Schema。Bearer 由服务端解析身份，不附加用户／组织头，不索取内部服务凭据或新增生成审批令牌。',
         '用户入口统一为宿主已配置的 Studio MCP，按[连接与能力发现](references/common-connection.md)确认本次需要的工具和 Schema。Bearer 由服务端解析身份，不附加用户／组织头，不索取内部服务凭据或新增生成审批令牌。'),
        ('用户明确要融合两种参考的音色：读取[实验性声谱融合](references/voice-morph.md)，使用同文源制作新的参考候选，再登记与跨文本试音；不宣称身份比例或稳定音色设计模型。',
         '用户明确要融合两种参考的音色：读取[实验性声谱融合](references/voice-morph.md)核对可用性；当前没有公开融合 MCP，本包只支持整理同文输入与已有候选，不执行后台实验脚本或宣称融合已可用。'),
        ('VoxCPM2 候选运行器处于开发验证，尚非 Studio 已开放能力。',
         '未出现在当前 Studio 工具与能力声明中的音色设计模型不可调用。'),
    ),
    'references/new-voice.md': (
        ('用户明确要把两种参考的音色结合成新候选时，可先走[实验性声谱融合](voice-morph.md)，再回到本流程登记。该路线有实际跨文本调用证据，但不保证音色稳定或自然度。',
         '用户明确要把两种参考的音色结合成新候选时，先核对[实验性声谱融合](voice-morph.md)的可用性。当前没有公开融合 MCP；可以整理输入或登记已有候选，不把后台实验视为本包已提供的能力。'),
        ('按[参考接收](voice-reference.md)核验录音。M4A、多声道或过长参考在已授权的 5090 dev 环境派生合格 WAV，记录原件摘要、处理和选段；不静默截断或覆盖。本机仅做静态检查。',
         '按[参考接收](voice-reference.md)及实际能力限制核验录音。本包的配置工具只接收合格 WAV；M4A、多声道或过长参考先按[普通参考派生](#21-普通参考派生)使用发行集合的受管工具生成独立 WAV，保留原件、处理和明确选段，不静默截断或覆盖，不连接内部开发环境。'),
        ('在技能目录运行下面的配置工具。路径替换为本项目实际输入，输出目录必须尚不存在。',
         '使用发行集合提供的受管 Python 运行时，在技能目录运行下面的配置工具；缺运行时的工程候选不能执行此步骤。路径替换为本项目实际输入，输出目录必须尚不存在。'),
        ('`speech.capabilities.json` 保存解包后的工具结果，不是完整 JSON-RPC 信封。',
         '`speech.capabilities.json` 保存解包后的完整工具结果（当前为 `engines` 数组），不是 JSON-RPC 信封。工具只选择唯一的 `indextts2` 声明，核对模型摘要、时长、大小、采样率及单声道限制；保留旧版平面能力快照兼容，拒绝混合或重复声明。后端未就绪时可以准备候选，但不得记为生成可用。'),
    ),
    'references/voice-reference.md': (
        (BASE + 'verdantflare-artifact/references/transfer-and-recovery.md', '../../verdantflare-artifact/references/transfer-and-recovery.md'),
        (BASE + 'verdantflare-project/references/revisions.md', '../../verdantflare-project/references/revisions.md'),
    ),
    'references/voice-design.md': (
        ('截至 2026-10-17，当前 IndexTTS2 没有音色设计字段；开发中的 VoxCPM2 候选运行器尚未通过 GPU 验证，也未作为 Studio 工具开放。',
         '当前 IndexTTS2 没有音色设计字段；未出现在 Studio 工具与能力声明中的设计引擎不可调用。'),
    ),
}
MORPH_START = '## 3. 在开发环境制作\n'
MORPH_END = '## 4. 登记与跨文本验证\n'
MORPH_PUBLIC = '''## 3. 公开入口与可用性

当前 Studio 未提供公开声谱融合 MCP；后台实验脚本和其科学计算运行时不在用户包中。不能要求用户进入内部开发环境、安装系统包或把普通 speech.create 当作融合接口。保留同文源及固定来源记录，可以继续已有参考复用、设计输入整理和已获得候选的登记准备。

没有实际可用的公开处理能力时，将新融合候选的生成保持未完成。未来只有工具发现、Schema、授权来源、依赖与真实验证齐备，才能依据该能力提交；本文不声明新的工具名、参数或可用日期，也不因此删减适配计划要求的验证范围。

后台实验产出的来源、参数、输入／输出摘要、诊断和待听审状态须随已有候选保存；其存在不证明当前用户能重现生成。输出的实际可懂度、声码器感、音色和跨文本稳定性仍分别核验。

'''

DERIVATION_GUIDE = '''### 2.1 普通参考派生

只有原件不符合实际参考限制或明确需要选段时使用 `scripts/derive_voice_reference.py`。以发行集合的受管 Python 执行；安装器提供已核验的 FFmpeg／FFprobe 绝对路径和摘要，不能从 PATH 猜工具或让用户手工安装系统依赖。工具摘要核对仅证明字节一致，不证明发布者身份。

Agent 从实际能力选择输出采样率与位深，并明确音轨索引、起点、时长和声道处理。下面只示意已明确选用第一个音轨的第一个声道，从 2 秒起取 6 秒；不要把该选段照搬到其他录音。变量由安装器上下文提供，不要求用户复制 Token 或编辑宿主配置。

```sh
"$VF_PYTHON" script scripts/derive_voice_reference.py \\
  --reference original.m4a --capabilities speech.capabilities.json \\
  --ffmpeg "$VF_FFMPEG" --ffmpeg-sha256 "$VF_FFMPEG_SHA256" \\
  --ffprobe "$VF_FFPROBE" --ffprobe-sha256 "$VF_FFPROBE_SHA256" \\
  --stream-index 0 --channel-index 0 --start 2 --duration 6 \\
  --sample-rate 24000 --bit-depth 16 --out reference-derived-v1
```

多声道等权混合须明确选用 `--mixdown`，与 `--channel-index` 互斥；混合后静音可能是相位抵消，拒绝后保留原件并选择正确声道，不自动增益补救。输入限普通本地 WAV、M4A／MP4、MP3、AAC、FLAC、OGG，至多 128 MiB；不支持的封装拒绝，不运行网络下载或播放列表。时长不足、输出不满足实际能力、静音、工具摘要不符、输入被修改或输出目录已有内容时均失败。Windows 私有目录／ACL 未验证前明确拒绝，不能据 macOS 成功宣称 Windows 支持。

成功产生独立 `reference.wav` 与 `derivation.json`，记录原件、实际流规格、明确处理、能力／模型／工具摘要及 PCM 测量；不保存本机路径。原件不变，不默认降噪、归一化、变速或融合。失败没有成功回执；可能产生的未完成候选保留，改用新的输出目录重试。将实际派生 WAV 按本章 Artifact 流程上传，再使用 `prepare_voice_profile.py` 准备候选；派生记录随项目保存，不能写入服务未声明字段。

本地能力快照不证明实时服务身份或时效，必须关联实际 MCP 调用。`candidate/pending` 表示仍待听审与登记；PCM 指标不能判断单一说话人、可懂度或噪声，转码也不代表真实 Speech／桌面业务通过。

'''


def source_key(name):
    if name not in FILES:
        raise ValueError('PUBLIC_FILE_NOT_ALLOWED')
    if name in ('references/common-connection.md', 'references/common-project-world.md'):
        return 'src/desktop-skill-delivery/public-docs/' + name.split('/')[-1]
    if name == 'scripts/derive_voice_reference.py':
        return 'src/desktop-skill-delivery/speech_tools/derive_voice_reference.py'
    return f'skills/{SPEECH}/{name}'


def transform_name(name):
    source_key(name)
    if name == 'references/new-voice.md':
        return 'speech-public-guide-v2'
    return 'speech-public-guide-v1' if name in REPLACEMENTS or name == 'references/voice-morph.md' else 'copy'


def transform(name, raw):
    if transform_name(name) == 'copy':
        return raw
    text = raw.decode('utf-8')
    for before, after in REPLACEMENTS.get(name, ()):
        if text.count(before) != 1:
            raise ValueError('PUBLIC_DOCUMENT_SOURCE_SHAPE_CHANGED')
        text = text.replace(before, after)
    if name == 'references/voice-morph.md':
        if text.count(MORPH_START) != 1 or text.count(MORPH_END) != 1 or text.index(MORPH_START) >= text.index(MORPH_END):
            raise ValueError('PUBLIC_DOCUMENT_SOURCE_SHAPE_CHANGED')
        text = text[:text.index(MORPH_START)] + MORPH_PUBLIC + text[text.index(MORPH_END):]
    if name == 'references/new-voice.md':
        anchor = '## 3. 登记与回读\n'
        if text.count(anchor) != 1:
            raise ValueError('PUBLIC_DOCUMENT_SOURCE_SHAPE_CHANGED')
        text = text.replace(anchor, DERIVATION_GUIDE + anchor)
    return text.encode('utf-8')
