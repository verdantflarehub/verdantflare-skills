"""Public Blender projection: packaged tools instead of private case scripts.

Engineering payload only; runtime delivery and native acceptance remain separate.
"""
from __future__ import annotations

import hashlib

BLENDER = 'verdantflare-blender'
FILES = ('SKILL.md', 'agents/openai.yaml', 'references/sessions-and-recovery.md',
         'references/project-save.md', 'references/set-export.md',
         'references/common-connection.md', 'references/common-project-world.md',
         'scripts/prepare_set_export.py', 'scripts/export_set_package.py',
         'scripts/validate_set_package.py', 'scripts/check_camera_evidence.py',
         'scripts/source-provenance.json')


def source_key(name):
    if name not in FILES:
        raise ValueError('PUBLIC_FILE_NOT_ALLOWED')
    if name.startswith('scripts/'):
        return 'src/desktop-skill-delivery/blender_tools/' + name.split('/')[-1]
    if name == 'references/set-export.md':
        return 'src/desktop-skill-delivery/public-docs/blender-set-export.md'
    if name in ('references/common-connection.md', 'references/common-project-world.md'):
        return 'src/desktop-skill-delivery/public-docs/' + name.split('/')[-1]
    return f'skills/{BLENDER}/{name}'


def transform_name(name):
    source_key(name)
    return 'blender-public-guide-v1' if name in ('SKILL.md', 'references/project-save.md', 'references/set-export.md') else 'copy'


def replace_once(text, before, after):
    if text.count(before) != 1:
        raise ValueError('PUBLIC_DOCUMENT_SOURCE_SHAPE_CHANGED')
    return text.replace(before, after)


def transform(name, raw):
    if transform_name(name) == 'copy':
        return raw
    text = raw.decode('utf-8')
    if name == 'SKILL.md':
        start = '- 需要导出供 Studio 网页片场查看器（`viewer`）使用的'
        end = '\n\n## 3. 保存、跨项目归档与验证'
        if text.count(start) != 1 or text.count(end) != 1 or text.index(start) >= text.index(end):
            raise ValueError('PUBLIC_DOCUMENT_SOURCE_SHAPE_CHANGED')
        if hashlib.sha256(text[text.index(start):text.index(end)].encode()).hexdigest() != 'fb230cf86e0b765f4e6e754311125c50d0a4880dda337459233d4ef70906c4b4':
            raise ValueError('PUBLIC_DOCUMENT_SOURCE_SHAPE_CHANGED')
        text = text[:text.index(start)] + '- 需要片场查看器包时，按[固定工程导出与机位核验](references/set-export.md)使用随包准备器、导出器、包校验器与截图测量器。脚本固定到授权 Project 修订，再通过实例 `script.run_project` 执行；源工程与脚本修订分别记录。普通场景保存不强加片场导出，包校验与数值测量不代替真实实例／网页验收。' + text[text.index(end):]
        text = replace_once(text, '先核对真实 tools/list、绑定 project_id 和任务范围。',
            '按[连接说明](references/common-connection.md)使用现有授权连接，先核对真实 tools/list、绑定 project_id 和任务范围。同一发行集合的 [Project](../verdantflare-project/SKILL.md) 与 [Artifact](../verdantflare-artifact/SKILL.md) 负责中央保存与回读；本地 helper 使用发行集合的受管 Python，图像测量另需 Pillow，未交付运行时不能声称这些脚本可用。')
        text = replace_once(text, '脚本（如 `blender/scripts/saw_set_v1.py`）', '脚本（如 `blender/scripts/prepared-set-export.py`）')
    elif name == 'references/set-export.md':
        for before, after in (('../../../skills/verdantflare-blender/references/project-save.md', 'project-save.md'),
                              ('../blender_tools/prepare_set_export.py', '../scripts/prepare_set_export.py'),
                              ('../blender_tools/validate_set_package.py', '../scripts/validate_set_package.py'),
                              ('../blender_tools/check_camera_evidence.py', '../scripts/check_camera_evidence.py')):
            text = replace_once(text, before, after)
    else:
        heading = '## 5. 三维片场包（Set Package v1.0）导出与校验\n'
        if text.count(heading) != 1:
            raise ValueError('PUBLIC_DOCUMENT_SOURCE_SHAPE_CHANGED')
        if hashlib.sha256(text[text.index(heading):].encode()).hexdigest() != '7b73911e4b93737de69b808a7a0c85a677df68096dae802800ecd6d51d3a2ded':
            raise ValueError('PUBLIC_DOCUMENT_SOURCE_SHAPE_CHANGED')
        text = text[:text.index(heading)] + heading + '\n片场包按[固定工程导出与机位核验](set-export.md)执行，使用同包 `scripts/` 工具，不读取团队仓库案例路径。源工程身份、脚本修订、输出修订与真实截图分别留证。Set Package 1.0／1.1、字节校验与真实画面验收分别核查；没有实际查看器采集和独立 Agent 复核时保持机位验收未完成。\n'
        text = replace_once(text, '没有宿主中央工具时，可用 Studio 0.5.50 的 studio-workspace call/fetch。',
            '没有宿主中央工具时，可使用发行集合提供且已验证兼容的 studio-workspace call/fetch；缺 CLI 时保留中央回读未完成，不要求用户克隆源码构建。')
    return text.encode('utf-8')
