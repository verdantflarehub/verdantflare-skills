---
name: verdantflare-music
description: 使用 VerdantFlare Music 创作、翻唱、换声、母带或局部重绘，也用于已有音乐项目续作及音乐制作技能的研发优化。
---

# VerdantFlare Music

先确定用户要完成的阶段，从已有项目记录与 Artifact 继续；只做歌词、企划或单项修复时不扩展成整曲制作。对同一请求中无需新艺术决定的准备、技术检查和后续工具调用连续执行。

用户要求研发或优化本技能时，处理通用工作流与能力契约，不以某首歌作为默认交付，也不自动运行音乐生成、转换或远端部署。先核对技能说明、本地服务实现、实际部署工具三者的差异；只把真实可用的能力写成可执行步骤。按 [技能研发检查](references/skill-development.md) 维护配置、文档与验证边界。

| 当前阶段                                     | 按需读取                                                                                                                                    |
| -------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------- |
| 进入远端生成、翻唱、训练或局部重绘           | [制作前预检](references/preflight.md)，核对人声来源、模型 ID、已批准范围、预算及实际 MCP 能力                                             |
| 企划、原创候选、翻唱、分轨、转换、对齐或返工 | [工作流](references/workflow.md) 的对应阶段                                                                                                 |
| 修复某一时间区间、尾奏或局部演唱             | [局部重绘](references/local-redraw.md)；仅在预检确认真实条件音频编辑后端可用时执行                                                           |
| 指定音色换声，或换声后细节流失               | [换声保真与返工](references/vocal-replacement-mix.md)；选定候选后先识别需要保留的层次，返工时复用已有资产                                     |
| 男女对唱、多人合唱或多声部换声               | [多声部制作](references/multi-vocal.md)；逐歌手确认声源、全曲对齐的独立轨与实际混音能力                                                       |
| 编写原创企划                                 | [全局规范](references/全局-审核规范.md)、[歌词规范](references/歌词-审核规范.md)、[编曲规范](references/编曲-审核规范.md)，再按下方曲风路由 |
| 输入输出、命名、目录或质量检查               | [交付契约](references/artifacts.md)                                                                                                         |
| 原始人声准备或训练                           | [人声准备](references/voice-preparation.md)                                                                                                 |
| 候选选择、批准、终审或恢复审核记录           | [审核门](references/review-gates.md)                                                                                                        |
| 配置问题                                     | [环境说明](../ENVIRONMENT.md)                                                                                                               |

音符驱动对唱进入预混或整曲前，按 [多声部制作](references/multi-vocal.md) 的 `duet-score-v1` 计划运行 `scripts/validate_duet.py`；自然演唱源换声前运行 `scripts/audit_performance_voice_source.py`。已有合格对唱基线的单声部替换可用 `scripts/replace_performance_duet_voice.py`；若须更换分离伴奏，先按 [多声部制作](references/multi-vocal.md) 判断能否用 `scripts/prepare_duet_replacement_backing.py` 恢复基线电平。逐句编排后用**上一版获认可的试听**作参照运行 `scripts/validate_performance_duet.py`。角色窗口轨通过不等于可独立重混的男女干声；需加伴奏轨核验能否重建试听。任一适用检查退出码非零时，只保留为诊断候选，不能调用母带或声部扩展；脚本通过也仍需逐句与整曲人耳听审。

当远端 Music MCP 无法稳定提供逐句歌手控制时，先检查是否已有歌词与旋律完整、听感自然的整曲源演唱；可按[多声部制作](references/multi-vocal.md)的自然演唱源路线进行分轨、音色转换和逐句编排。没有合格源演唱时才制作新的短样；OpenUtau 路线仅验证过双轨渲染，旧完整歌曲已被听审否决。不能把单轨提示词生成结果或未经听审的换声预混标为合格对唱。

默认男女对唱采用“女声/男声接力，最后副歌合流”的 `call_response_final_merge` 模板；`both` 不是副歌的默认标签。参考拆解见 [《水晶》对唱参考](references/duet-reference-water-crystal.md)。

曲风只加载当前需要的一组：

- R&B：[歌词](references/歌词-R&B-审核规范.md) / [编曲](references/编曲-R&B-审核规范.md)
- 说唱：[歌词](references/歌词-说唱-审核规范.md) / [编曲](references/编曲-说唱-审核规范.md)
- 华语流行摇滚：[歌词](references/歌词-华语流行摇滚-审核规范.md) / [编曲](references/编曲-华语流行摇滚-审核规范.md)
- 英式摇滚：通用歌词 / [编曲](references/编曲-英式摇滚-审核规范.md)
- 乡村音乐：通用歌词 / [编曲](references/编曲-乡村音乐-审核规范.md)
- 现代电子：通用歌词 / [编曲](references/编曲-现代电子-审核规范.md)
- 日式二次元燃系：通用歌词 / [编曲](references/编曲-日式二次元燃系-审核规范.md)

其他曲风使用通用规范并记录风格假设，不因缺少专属文件停止企划。融合曲风只加载用户明确要求的相关规范，并先写清主风格与辅助元素。

写企划不依赖远端预检。进入远端阶段时优先使用可用的 `workflow.preflight`；旧版 Music MCP 没有此工具时按 [预检](references/preflight.md) 的兼容路径检查当前阶段，不把“工具不存在”写成服务故障，也不伪称预检通过。人声显示名不能代替实际模型 ID；局部重绘必须有真实编辑能力。

通过已配置的 Music MCP 和受控 Artifact 执行；宿主未注入工具时可使用该 MCP 的标准客户端，不直连供应商。训练使用有权处理且已批准的材料，已批准模型直接复用。只在确需用户听审或新增授权的地方暂停；候选选定后，分轨技术检查通过即可继续已批准模型的转换，不单独索取分轨批准。

制作状态只写入 [制作审核记录与资产清单](references/artifacts.md)：追加关键用户决定、版本、任务和 Artifact ID，不为每次工具调用另建报告。收费生成与重做受已批准数量/预算约束，不覆盖旧资产或伪造 LRC。按 [审核门](references/review-gates.md) 只执行当前任务适用的人工审核；工具成功不能代替音质终审。
