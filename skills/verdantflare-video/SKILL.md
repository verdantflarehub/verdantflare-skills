---
name: verdantflare-video
description: 通过 Studio Video MCP 的真实动态渠道生成、恢复和下载视频，按已注册能力使用 H3、SD2、Grok、Google 与 Higgsfield 模型；核实参数、素材限制和报价并保留具体服务错误。
metadata:
  version: "0.5.3"
---

# VerdantFlare Video

AI 短剧任务形成阶段成果后默认同步到原 Studio 项目，沿用真实 project_id；用户指定仅本地或只读讨论时遵循其范围。候选、返修与最终结果保留固定版本、修改原因、来源设定和任务记录，按当前 Project / Artifact 契约提交并回读修订；只上传或本地保存不算已同步。响应未知先对账恢复，不重复收费生成。同步不自动批准、选用或发布 World。

本 Skill 通过 Studio MCP 统一网关调用 Video 能力。用户未指定模型或渠道时，解析当前宿主配置并核实可用性；不把历史环境的 `h3-vdn`、`h3-sol` 或工具 Schema 中的 `fal` 默认值当成全局生产默认。用户可指定模型简称（如 H3、SD2）与渠道；只有宿主已接入的组合才能提交，失败不静默换渠道。

Project、Artifact 和 World 的生命周期遵循[共同接入规则](https://github.com/verdantflarehub/verdantflare-skills/blob/verdantflare-video-v0.4.1/skills/_shared/project-world.md)。提交前若输入来自 World，先解析并授权固定 `asset_id + asset_version_id`，再使用其中明确的 `ContentRef`；Video 输出先归档到当前 Video Project，Skill 不直接注册或升级 World 资产。

H3 Ref2VA 使用业务模型 `minimax-h3-ref2va`；`route` 单独指定推理渠道。fal 适配器使用服务端锁定的 Reference-to-Video endpoint，但注册了工具不代表fal已配置或就绪。供应商凭据留在服务端，Skill 不持有或发送 `FAL_KEY`。

创建前沿用用户名称，或用已有创作上下文生成简洁中文 Title（建议 8–16 字，单行 1–64 字），允许同名。面向用户使用服务端返回的 Title；技术 ID 保留在结构化 journal 与恢复记录。命名不改变 Prompt、幂等键或生成单元，resume 不重新 create 或补题。调用与兼容边界见 [MCP 调用说明](references/video-mcp.md)。

先用真实Studio会话发现工具，按 [MCP调用说明](references/video-mcp.md) 解析当前注册名（现有网关为 `video.create/status/result/rate_content`）与参数。遇到403或管理工具不可见，按 [环境说明](https://github.com/verdantflarehub/verdantflare-skills/blob/verdantflare-video-v0.4.0/skills/ENVIRONMENT.md) 检查是否误用了旧共享媒体令牌；不立即推断Video服务不可用。

`scripts/video_client.py` 的新生成统一走 Studio：分页发现 `video.create`（兼容已注册的 `video.generate`）及输入 Schema，通过 `video.capabilities` 精确匹配渠道＋模型（含 `models[]` 别名）＋模式。没有能力声明、声明冲突或缺少参数 Schema 时停止创建。素材优先通过分块工具可恢复导入，兼容旧 `video.import` 小图片入口；导入成功不等于满足当前模型的参考限制。已注册 `video.preflight` 时在创建前调用，`can_submit=false` 必须停止。预检不等于生成实测。具体参数、预算与恢复见 [执行流程](references/workflow.md)。本版本与同名公共 API 版 Skill 的认证和参数不同，不能混用。

H3 模型支持六种画幅，Omni Reference 还有 Auto；渠道适配器里的竖屏硬编码是接入缺口，不能据此宣称模型只支持9:16。模型规格、当前代码接入范围、实时配置和GPU实测必须分别说明。

- H3 生成单元：按当前阶段读取 [输入模式](references/input-modes.md)、[提示词](references/prompting.md)、[MCP 契约](references/video-mcp.md)、[执行流程](references/workflow.md) 或 [校验](references/validation.md)。
- 生成后 18+ 内容分级评估（K09 强制步骤）：每次取得新生成的视频产物后、在交付摘要展示与 Project/World 同步之前，必须按 [评估步骤](references/content-rating-step.md) 与 [评估规范](references/content-rating-rubric.md) 在本机临时目录抽帧（至少每 2 秒 1 帧、全片至少 8 帧，另补镜头切换处关键帧；评估后删除临时帧文件，并在 `reasons` 中记录抽帧数量），结合连续帧序列、音轨、正向提示词与标题完成判定，调用 `video.rate_content` 提交分级结果（`general` 与 `nsfw` 均须提交，`rubric_version` 取自规范副本文首；不确定即标 `nsfw`；超时用相同内容幂等重试，不重新生成）。判定为 `nsfw` 的产物在摘要中只写“N 个产物已标记为 NSFW 并隐藏”，不展示预览、联系表、链接、`ContentRef` 或提示词全文，也不同步到 Studio 项目或登记到 World。
- SD2：读取 [SD2 工作流](references/sd2-workflow.md)。
- Grok：先读取 [Grok 调用](references/grok.md)，通过同一 Studio Video MCP 显式选择 `route=grok`；不要套用 H3 的参考数量、24 FPS 或提示词标签。
- Google：读取 [Google 调用](references/google.md)，按实际注册的新模型与别名选择；退役模型返回 `model_retired` 时保留原错误，不自动切换收费模型。
- Higgsfield：读取 [会员渠道与官方 Skills 适配](references/higgsfield.md)，通过同一 Studio Video MCP 显式选择 `route=higgsfield`。Seedance 2.5 Draft 的 R2V／ref2va 使用 `model=seedance_2_5_draft`、480p、4–15 秒、最多 4 图＋1 视频，不替换为普通 2.5 或首尾帧。官方 CLI 封装在本 Skill 私有运行时，仅供管理员授权维护；成员生成、查询及结果通过 Studio，会员凭据留在服务器。
- 已有任务沿原任务记录的模型与渠道恢复，不按当前默认值重新生成。
- 有剧情动作、连续运镜、跨镜物体或剪辑选段要求时，按[叙事动作验收](references/shot-acceptance.md)检查实际动作和可用区间，不能用首尾构图或技术成功代替动态验收。
- H3-Sing 可选 `postprocess.interpolate.profile=rife_2x`（CLI `--interpolate`），默认关闭；已有视频使用同一 Video MCP 的 `video.interpolate`。开启时等待 `delivery.status` 终态，交付 `delivery.final_artifact_id`，保留原片。插帧失败只对原片明确重试，不重跑成功的生成，详见调用与校验说明。
- 每次创建任务都显式传入当前项目的 `project_id`、已解析的 `route` 和固定幂等键；不能依赖服务端猜测项目或渠道。
- 中央Artifact的 `ContentRef` 与Video原生引用按宿主适配流程转换；不直接替换ID，不把受控下载路径当成匿名公网素材URL。
- 所有模型和渠道都通过 Studio MCP 统一网关进入，不直接调用 Runtime。
