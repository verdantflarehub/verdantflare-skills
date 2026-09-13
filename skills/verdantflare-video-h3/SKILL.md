---
name: verdantflare-video-h3
description: 执行 MiniMax H3 生成单元的提示词编译、视频生成、查询、恢复和下载；不编排整支 MV。
---

# VerdantFlare Video H3

将冻结的 `GenerationUnit` 执行为可审阅 `ShotCandidate`。用户直接请求 H3 时可先准备输入草案；缺少批准或冻结输入时只阻止提交。

| 当前操作                               | 按需读取                                   |
| -------------------------------------- | ------------------------------------------ |
| 选择 Sol/原版、确认能力或构造 MCP 请求 | [MCP 契约与路线](references/video-mcp.md)  |
| 区分首帧、首尾帧和全参考输入           | [输入模式](references/input-modes.md)      |
| 编译动态提示词                         | [提示词](references/prompting.md)          |
| 判断控制参数可靠性                     | [控制证据](references/control-evidence.md) |
| 提交、恢复、重试与登记候选             | [执行流程](references/workflow.md)         |
| 检查输入与媒体结果                     | [校验](references/validation.md)           |
| 配置问题                               | [环境说明](../ENVIRONMENT.md)              |

默认 Sol-H3，明确原版请求使用宿主的非 Sol 路线；不能静默回退。`h3-sol` 是路线，不臆造 `model` 参数；业务模型类型统一为 `minimax-h3-ref2va`。具体运行时必须另用宿主声明的 route identity 区分：当前 A 为 `minimax-h3-sol-ref2va`，4090 实验 B 为 `minimax-h3-sol-ref2va-4090`。提交前同时确认 `model` 类型和实际 route identity 映射，不能把 B 的 route identity 填入 `model` 字段。

当前单元为 4–15 秒，至少包含图片或视频 Artifact，不能只有音频。通过 `video.generate/status/result` 执行，不绕道 Runtime。冻结的创作输入变化须新建版本；状态未知时恢复原任务，不重复收费。

自主完成已授权的准备、下载、校验和技术修复。技术校验完成后交付候选、任务引用与证据；候选选择和最终 MV 批准由调用方完成。
