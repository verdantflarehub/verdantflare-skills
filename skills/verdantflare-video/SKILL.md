---
name: verdantflare-video
description: 统一通过 Video MCP 选择视频模型与渠道，执行生成、查询、恢复和下载；支持 H3、SD2 及后续视频渠道。
---

# VerdantFlare Video

本 Skill 只有一个 Video MCP 入口。用户未指定模型时使用宿主配置的默认模型与渠道；当前生产默认模型为 H3，默认渠道为 `h3-vdn`。用户可以显式指定已由宿主能力声明支持的模型简称 `model`（如 `h3`、`sd2`）与推理渠道 `route`（如 `h3-vdn`），Skill 不把渠道写死，也不静默回退。

- H3 生成单元：读取 [H3 输入模式](references/input-modes.md)、[提示词](references/prompting.md)、[MCP 契约](references/video-mcp.md)、[执行流程](references/workflow.md) 和 [校验](references/validation.md)。
- SD2：读取 [SD2 工作流](references/sd2-workflow.md)。
- 已有任务沿原任务记录的模型与渠道恢复，不按当前默认值重新生成。
- 所有模型和渠道都必须通过同一个 Video MCP，不直接调用 Runtime。
