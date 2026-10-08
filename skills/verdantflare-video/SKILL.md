---
name: verdantflare-video
description: 统一通过 Video MCP 选择视频模型与渠道，执行生成、查询、恢复和下载；支持 H3、SD2 及后续视频渠道。
---

# VerdantFlare Video

本 Skill 通过 Studio MCP 统一网关调用 Video 能力。用户未指定模型或渠道时，解析当前宿主配置并核实可用性；不把历史环境的 `h3-vdn`、`h3-sol` 或工具 Schema 中的 `fal` 默认值当成全局生产默认。用户可指定模型简称（如 H3、SD2）与渠道；只有宿主已接入的组合才能提交，失败不静默换渠道。

Project、Artifact 和 World 的生命周期遵循[共同接入规则](../_shared/project-world.md)。提交前若输入来自 World，先解析并授权固定 `asset_id + asset_version_id`，再使用其中明确的 `ContentRef`；Video 输出先归档到当前 Video Project，Skill 不直接注册或升级 World 资产。

H3 Ref2VA 使用业务模型 `minimax-h3-ref2va`；`route` 单独指定推理渠道。fal 适配器使用服务端锁定的 Reference-to-Video endpoint，但注册了工具不代表fal已配置或就绪。供应商凭据留在服务端，Skill 不持有或发送 `FAL_KEY`。

先用真实Studio会话发现工具，按 [MCP调用说明](references/video-mcp.md) 解析当前注册名（现有网关为 `video.create/status/result`）与参数。遇到403或管理工具不可见，按 [环境说明](../ENVIRONMENT.md) 检查是否误用了旧共享媒体令牌；不立即推断Video服务不可用。

`scripts/video_client.py` 的新生成统一走 Studio：发现 `video.create`（兼容已注册的 `video.generate`），通过 `video.capabilities` 解析默认渠道和适配器限制。素材优先通过 `video.import_prepare/import_chunk/import_status/import_commit` 可恢复导入，兼容旧 `video.import` 小图片入口。Singularity 用 `video.preflight` 返回实际画布、时长和参考编号；预检通过不代表 GPU 实测通过。没有能力工具的旧宿主须显式指定已核实的渠道。历史 SD2 仅保留恢复兼容，新 SD2 使用宿主 MCP 工具。具体命令见 [执行流程](references/workflow.md)。

H3 模型支持六种画幅，Omni Reference 还有 Auto；渠道适配器里的竖屏硬编码是接入缺口，不能据此宣称模型只支持9:16。模型规格、当前代码接入范围、实时配置和GPU实测必须分别说明。

- H3 生成单元：按当前阶段读取 [输入模式](references/input-modes.md)、[提示词](references/prompting.md)、[MCP 契约](references/video-mcp.md)、[执行流程](references/workflow.md) 或 [校验](references/validation.md)。
- SD2：读取 [SD2 工作流](references/sd2-workflow.md)。
- 已有任务沿原任务记录的模型与渠道恢复，不按当前默认值重新生成。
- 每次创建任务都显式传入当前项目的 `project_id`、已解析的 `route` 和固定幂等键；不能依赖服务端猜测项目或渠道。
- 中央Artifact的 `ContentRef` 与Video原生引用按宿主适配流程转换；不直接替换ID，不把受控下载路径当成匿名公网素材URL。
- 所有模型和渠道都通过 Studio MCP 统一网关进入，不直接调用 Runtime。
