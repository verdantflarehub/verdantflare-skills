---
name: verdantflare-character-face
description: 将正脸身份参考经视频取姿态、抽帧和正脸参考纠正，制作角色多角度参考库；支持回头与高低机位补图、局部表情或肤质修复及项目归档。用于角色锁脸和多角度参考制作，不用于精确三维重建或一般单图美化。
---

# 角色锁脸与多角度参考（verdantflare-character-face）

## 1. 范围与边界

本技能将正脸身份参考经视频取姿态、抽帧与正脸参考纠正，制作角色多角度参考库与角色设定页（半身照＋无头三视图），并支持回头与高低机位补图、局部表情或肤质修复及 Studio 项目归档。适用于角色锁脸与多角度参考制作；不用于精确三维重建或一般单图美化。

本技能按阶段协同已安装的 `verdantflare-image`（默认 GPT Image 路线）、Studio 版 `verdantflare-video`（核对 Studio 客户端契约标识，不混用公共 API 同名技能）、`verdantflare-artifact` 与 `verdantflare-project`；依赖缺失时说明缺项并继续不依赖它的整理，不臆造工具或绕过 Studio。角色年龄、姓名、体型、表情、图数与目标角度均取当前任务，历史案例参数不成为默认硬约束。

## 2. 能力清单与按任务读取

按要执行的原子能力直达对应入口；锁脸红线、坐标定义、实验对照与 `audit_images.py` 技术审计规则见 `00.common.rules.md`：

| 原子能力（能干什么活） | 入口文档                                                                        |
| :--------------------- | :------------------------------------------------------------------------------ |
| 多角参考库             | [01.multi-angle-lib.entrypoint.md](references/01.multi-angle-lib.entrypoint.md) |
| 补图精修               | [02.local-repair.entrypoint.md](references/02.local-repair.entrypoint.md)       |

新建、补角度、局部修复与仅入库分别处理，先取证再接续，不让每次请求重新走完整生产链。

跨能力公共文档：[00.common.rules.md](references/00.common.rules.md)（由各能力入口引用）。

## 3. 工具与命令

包内[图片取证脚本](scripts/audit_images.py)（依赖 Python 3.10+ 与 Pillow）用于对明确清单实测解码尺寸、SHA-256、编号重复与缺图；脚本不识别人脸、不判定相机角度，也不代替内容分级或用户批准：

```text
python -X utf8 scripts/audit_images.py /path/to/manifest.json --output /path/to/audit.json
```

退出码 `0` 表示技术检查通过，`1` 表示存在失败项，`2` 表示清单或依赖不可用；清单 JSON 格式、五项技术验收条件与视觉对照规则见[公共规则](references/00.common.rules.md)。
