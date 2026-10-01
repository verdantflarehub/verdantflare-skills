---
name: verdantflare-video
description: 统一通过 Video MCP 选择视频模型与渠道，执行生成、查询、恢复和下载；支持 H3、SD2 及后续视频渠道。
---

# VerdantFlare Video

本 Skill 只有一个 Video MCP 入口。用户未指定模型时使用宿主配置的默认模型与渠道；当前生产默认模型为 H3，默认渠道为 `h3-vdn`。用户可以显式指定已由宿主能力声明支持的模型简称 `model`（如 `h3`、`sd2`）与推理渠道 `route`（如 `h3-vdn`、`fal`），Skill 不把渠道写死，也不静默回退。

`fal` 已开放为 H3 Ref2VA 的显式渠道，固定使用业务模型 `minimax-h3-ref2va` 和服务端 endpoint `minimax/h3/reference-to-video`。它不是默认渠道；提交前必须确认宿主 MCP 声明 `route=fal` 可用。凭据缺失、渠道拒绝或状态不确定时保留原任务记录并停止，不得回退到 `h3-vdn`、`h3-sol` 或其他渠道，也不得由 Skill 持有或发送 `FAL_KEY`。

命令行统一使用 `scripts/video_client.py`。选择 `model=minimax-h3-ref2va`、`route=fal` 时，该客户端读取 `VIDEO_MCP_URL` 和 `VIDEO_MCP_BEARER_TOKEN`，通过 MCP 工具完成 Artifact 导入、生成、查询、结果读取和恢复；不另设 fal 专用客户端，也不把 fal 请求误投到 SD2 公共 API。

- H3 生成单元：
  -- 读取 [H3 输入模式](references/input-modes.md)
  -- [提示词](references/prompting.md)、
  -- [MCP 契约](references/video-mcp.md)、
  -- [执行流程](references/workflow.md)
  -- [校验](references/validation.md)。
- SD2：读取 [SD2 工作流](references/sd2-workflow.md)。
- 已有任务沿原任务记录的模型与渠道恢复，不按当前默认值重新生成。
- 每次 `video.generate` 都显式传入当前项目的 `project_id` 和已解析的 `route`；不能依赖服务端猜测项目或渠道。
- 所有模型和渠道都必须通过同一个 Video MCP，不直接调用 Runtime。
