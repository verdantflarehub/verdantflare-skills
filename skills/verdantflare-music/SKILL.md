---
name: verdantflare-music
description: 使用 VerdantFlare Music 创作歌曲或影视器乐配乐、翻唱、换声、母带及局部重绘，也用于已有音乐项目续作与技能研发优化。
metadata:
  version: "0.6.1"
---

# VerdantFlare Music

## 1. 范围与边界

本技能通过已配置的 Studio Music MCP 与受控 Artifact 执行原创歌曲、影视器乐配乐、翻唱/扒谱、男女对唱与多人合唱、RVC 人声训练与换声、局部重绘与母带制作，并覆盖已有音乐项目续作与技能研发边界。AI 短剧成果默认同步原 Studio 项目（沿用真实 `project_id`），歌曲/分轨/母带默认留来源 Project，仅明确可复用的已批准人声模型登记至 World/Music 并固定引用 `asset_id + asset_version_id`。

先确定当前阶段并从已有项目记录续做，只做歌词、企划或单项修复时不扩成整曲；响应未知先对账恢复，不重复收费生成；同步不自动批准或发布 World。本地 GPU 推理（`MiniMax Music 3`、`ACE-Step`、`UVR5`、`RVC`）由 Music MCP 经 Station GPU 调度器（`:5056`）自动申请租约并冷启动（`0 -> 1`），`queued`（`waiting_gpu_lease`）继续轮询原任务 ID，严禁重复提单或关停其他 GPU 负载。

## 2. 能力清单与按任务读取

每次只进入一个原子能力入口；组合多能力时先完成前一项输出与验证再传给下一项：

| 原子能力（能干什么活）   | 入口文档                                                                    |
| :----------------------- | :-------------------------------------------------------------------------- |
| 原创配乐                 | [01.song-compose.entrypoint.md](references/01.song-compose.entrypoint.md)   |
| 翻唱、曲谱约束翻唱与扒谱 | [02.cover-score.entrypoint.md](references/02.cover-score.entrypoint.md)     |
| 男女对唱与多人合唱编排   | [03.duet-ensemble.entrypoint.md](references/03.duet-ensemble.entrypoint.md) |
| RVC 换声                 | [04.voice-replace.entrypoint.md](references/04.voice-replace.entrypoint.md) |
| 重绘母带                 | [05.redraw-master.entrypoint.md](references/05.redraw-master.entrypoint.md) |

先读目标能力的 `entrypoint.md`（输入与输出 → 分类 → 工作流 → 规范 → 验收），无需通读其他能力。配置问题读 [ENVIRONMENT.md](../ENVIRONMENT.md)。

跨能力公共文档：[00.common.rules.md](references/00.common.rules.md)、[00.common.workflow.md](references/00.common.workflow.md)、[00.common.acceptance.md](references/00.common.acceptance.md)（由各能力入口引用）。

## 3. 工具与命令

本地确定性审计与对唱脚本位于 `scripts/`（需 `numpy`、`scipy`、`soundfile`；掩膜脚本另需 `librosa`；完整参数见对应能力入口）：

- [analyze_tail.py](scripts/analyze_tail.py)：候选时长与尾部硬切/坍缩检查。
- [validate_score_plan.py](scripts/validate_score_plan.py)：影视贴片线性配乐计划检查（仅用标准库）。
- [validate_duet.py](scripts/validate_duet.py) / [validate_performance_duet.py](scripts/validate_performance_duet.py)：音符驱动与自然源对唱机器校验。
- [audit_performance_voice_source.py](scripts/audit_performance_voice_source.py) / [replace_performance_duet_voice.py](scripts/replace_performance_duet_voice.py) / [prepare_duet_replacement_backing.py](scripts/prepare_duet_replacement_backing.py)：自然源对唱声部审计、单声部替换与伴奏电平校准。
- [lift_duet_singer_mask.py](scripts/lift_duet_singer_mask.py) / [replace_singer_in_mix.py](scripts/replace_singer_in_mix.py)：原混音同唱全频掩膜分离与局部换声。

```sh
python3 scripts/validate_score_plan.py score-plan.json
python3 scripts/validate_duet.py --plan plan.json --female female.wav --male male.wav --instrumental inst.wav --report val.json
```
