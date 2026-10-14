---
name: verdantflare-speech
description: 通过 VerdantFlare Studio 生成中文对白和旁白，使用固定参考声线逐句配音、恢复任务并归档试听。用于角色配音、短录音文稿、声线复用和单句返修；歌曲、RVC 换声、环境音效及口型驱动交由对应能力。
metadata:
  version: "0.2.0"
---

# VerdantFlare Speech

把固定台词、参考声音和每次实际生成绑定为可追溯的 take。沿用当前项目、参考版本和用户授权；只改目标台词无需重新录制声音。

## 1. 能力发现

用户入口统一为宿主 Studio MCP，读取 `STUDIO_MCP_URL` 与 `STUDIO_MCP_BEARER_TOKEN`，从实际工具目录确认本次需要的能力和 Schema。Bearer 由服务端解析身份，不附加用户／组织头，不索取内部服务凭据或新增生成审批令牌。

2026-10-14 已通过 Studio 验证 Speech 0.1.1 → IndexTTS2 0.1.3 → Artifact 原生产物 → Project 固定修订。实际工具为 `speech.capabilities`、`speech.voices`、`speech.create`、`speech.status`、`speech.result`。每次执行重新发现 Schema 和就绪状态；缺工具时报告当前入口缺口，继续可独立完成的准备和已有结果整理。

本技能不启动 GPU 服务、不读取 Kubernetes Secret，也不直连内部运行时。Music 歌曲接口或视频自带音轨不自动替代独立对白生成。

## 2. 选择工作

- 用户要短录制文稿、提交录音或复用声线：读取[参考接收与版本](references/voice-reference.md)。
- 用户要逐句配音、恢复任务、返修或保存试听：读取[任务与交付](references/tasks-and-delivery.md)。
- 仅讨论文本或明确仅本地时遵循该范围；不因缺少生成工具而停止独立准备。

## 3. 制作边界

先固定原台词与角色，不自行改写、拆换说话人或把表演说明念进正文。只传实际后端支持的参数；不承诺精确时长、非语言表演或音频驱动口型。换文本、参考或有效参数形成新 take，保留旧结果。

参考音频是 voice profile 的版本化组成，不能伪称为新训练权重；RVC 权重不能直接加载到 IndexTTS2。原始录音、权重、生成媒体和凭据不进入技能包或 Git。

生成成功、字节校验、项目归档、声线选用与人耳听审分别报告。满幅或明显失真保留问题标记，不能靠后期降音量宣称已恢复削平波形。候选归档不自动批准或发布 World。
