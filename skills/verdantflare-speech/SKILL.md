---
name: verdantflare-speech
description: 通过 VerdantFlare Studio 从 World 声线资产或项目参考录音生成对白、旁白和新文稿，支持发音词典、情绪控制、逐句换声、过期 take 检出、听审返修和归档。也用于基于参考特征规划音色变化、准备新声线候选；歌曲与 RVC 换声交由 Music。
metadata:
  version: "0.4.0"
---

# VerdantFlare Speech

## 1. 范围与边界

本技能通过宿主 Studio MCP（`STUDIO_MCP_URL` + `STUDIO_MCP_BEARER_TOKEN`）将固定台词、参考声音与每次实际生成绑定为可追溯的 take，覆盖对白/旁白配音、发音词典与情绪控制、参考录音登记、音色设计规划与实验性声谱融合。沿用当前项目、声线版本与用户授权；仅改目标台词或词典无需重录参考；仅讨论文本或明确仅本地时遵循该范围。

已接通 Speech 0.2.0 → IndexTTS2 0.2.0 → Artifact 原生产物 → Project 固定修订。本技能不启动 GPU 服务、不读取 Kubernetes Secret、不直连内部运行时；`speech.create` 调用 IndexTTS2 时由 Speech MCP 自动向 Station GPU 调度器（`:5056`）申请 `6144 MiB` 显存租约并执行按需冷启动（`0 -> 1`），`queued`（`waiting_gpu_lease`）或 `phase: loading_model` 属正常排队等待，继续轮询原任务 ID，不重复提交。歌曲与 RVC 换声交由 `verdantflare-music`，视频原生对白与口型另查 `verdantflare-video`。

## 2. 能力清单与按任务读取

按要执行的原子能力直达对应入口；跨能力共享的 MCP 交付、过期 take 检出、`report.json` v2 与 K09 内容分级规范见 `00.common.rules.md`：

| 原子能力（能干什么活）   | 入口文档                                                                              |
| :----------------------- | :------------------------------------------------------------------------------------ |
| 对白旁白、词典与情绪控制 | [01.dialogue-narration.entrypoint.md](references/01.dialogue-narration.entrypoint.md) |
| 声线设计                 | [02.voice-design.entrypoint.md](references/02.voice-design.entrypoint.md)             |

先读目标能力的 `entrypoint.md`（输入与输出 → 分类 → 工作流 → 规范 → 验收），无需通读其他能力。

跨能力公共文档：[00.common.rules.md](references/00.common.rules.md)（由各能力入口引用）。

## 3. 工具与命令

宿主 Studio MCP 语音工具为 `speech.capabilities`、`speech.voices`、`speech.create`、`speech.status`、`speech.result`、`speech.rate_content`（每次执行重新发现 Schema 与就绪状态）。本技能附带两份本地确定性工具脚本（完整参数与前置条件见 [02.voice-design.entrypoint.md](references/02.voice-design.entrypoint.md)）：

- [prepare_voice_profile.py](scripts/prepare_voice_profile.py)：核验参考 WAV 并生成符合契约的 `profile.json` 与 `intake.json`（仅用 Python 标准库）。
- [morph_voice_reference.py](scripts/morph_voice_reference.py)：在已授权 5090 dev 环境对同文源执行 WORLD 声谱插值融合（需 `numpy/scipy/soundfile/pyworld`）。

```sh
python3 scripts/prepare_voice_profile.py --reference reference.wav --content-ref reference.contentref.json --capabilities speech.capabilities.json --voice-id narrator-b --label 旁白B --out voice-candidate-v1
python3 scripts/morph_voice_reference.py --base source-a/original.wav --base-generation source-a/generation.json --donor source-b/original.wav --donor-generation source-b/generation.json --weight 0.35 --out morph-candidate-v1
```
