---
name: verdantflare-image
description: 通过 VerdantFlare Image MCP 生成或编辑图像候选，包括角色视图、服装人台、分镜单格和局部重绘。
---

# VerdantFlare Image

已绑定 Studio 项目的图像创作与返修默认完成素材传输、原项目保存及角色关联核验，沿用真实 project_id；用户无需另说同步或去网站检查。仅本地、只讨论或暂不上传的明确限定优先。按当前 Project / Artifact 契约提交并回读；只上传或本地保存不算已同步。响应未知先对账恢复，不重复收费生成；同步不自动批准、选用或发布 World。素材添加、查询、删除交由 Project 流程处理，不因此启动新的生图。

交付具有不可变 Artifact 与 SHA-256 的图像候选；不负责 MV 导演或剪辑。仅解释或修改代码时不启动生图。

Project 归属、换电脑恢复、Artifact 保存及 World 固定资产引用遵循[共同接入规则](../_shared/project-world.md)。生成或导入结果先保存到当前 Project；确认可复用后，按 [World / Character 形象基线](../../../docs/design/world/character/identity.md) 显式登记固定版本。

| 当前操作                               | 按需读取                                                                                        |
| -------------------------------------- | ----------------------------------------------------------------------------------------------- |
| 构造生成、编辑、遮罩或导入请求         | [调用契约](references/contracts.md) 的对应工具章节                                              |
| MOSS-Image 文字海报与 JSON 版式文生图  | [MOSS-Image 调用](references/moss-image.md)                                                     |
| 角色四视图、服装三视图、分镜或场景规格 | [视觉规格](references/visual-specs.md) 对应资产类型                                             |
| 提交、轮询、恢复、下载与归档           | [执行流程](references/workflow.md)                                                              |
| 生成后 18+ 内容分级评估与标记          | [评估步骤](references/content-rating-step.md)、[评估规范](references/content-rating-rubric.md) |
| 使用本地客户端                         | [CLI](references/cli.md)；参数不确定时运行 `--help`                                             |
| 连接或配置问题                         | [环境说明](../ENVIRONMENT.md)                                                                   |

默认 codex / `gpt-image-2.5-sunburst`；用户明确选择优先，模型支持以宿主能力和调用契约为准。不绕过 MCP/受控客户端直连供应商。

Grok 生成与单图编辑读取 [Grok 调用](references/grok.md)，以 `image.capabilities` 的实际模型为准；现有 REST CLI 不用于 Grok，使用 Studio MCP。

MOSS-Image 自托管文生图（`engine="moss-image"`）读取 [MOSS-Image 调用](references/moss-image.md)，经同一 Studio Image MCP 调用，面向中英文密集文字海报与原生 JSON 版式控制（`layout` 采用 `yxyx` 与 `abs_centralized` 坐标空间，`items` 最多 32 项）；支持 `width` / `height`（16 的整数倍，总像素 0.25M–4M，默认 1024×1024）、`steps` / `guidance` / `seed` 与独立 `negative_prompt`。该引擎不经渠道应用，不传 `channel_account_id`、`budget_limit`、`prefer_4k`、`reference_images`、`mask` 或编辑/局部重绘参数（传入会以 `INVALID_ARGUMENT` 拒绝）。

使用真实Studio会话发现实际工具；现有网关提供 `image.create/edit/inpaint/status/result/rate_content`，不假定历史 `image.generate` 或 `image.list` 已注册。旧共享媒体令牌不能访问Project/Artifact，认证恢复见 [环境说明](../ENVIRONMENT.md)。

中央Artifact引用与Image原生Artifact ID需要受控适配，不因同名字段就互换。附带的 `scripts/image_client.py` 仍是REST兼容客户端，不能把Studio `/mcp` 地址当其REST根地址；具体限制见 [CLI](references/cli.md)。

描述目标画面、必要参考职责及编辑保留项即可；四段式提示词只是模板，不要求固定标签、句式或每轮只改一个变量。

生成后内容分级评估（K09 强制步骤）：取得生成产物后、在交付摘要展示与 Project/World 同步之前，必须按 [评估步骤](references/content-rating-step.md) 与 [评估规范](references/content-rating-rubric.md) 评估图像本身、正向提示词与标题，并调用 `image.rate_content` 提交分级结果（`general` 与 `nsfw` 均须提交，`rubric_version` 取自规范副本文首；不确定即标 `nsfw`；提交超时用相同内容幂等重试，不重新生图）。判定为 `nsfw` 的产物在摘要中只写“N 个产物已标记为 NSFW 并隐藏”，不展示预览、链接、`ContentRef` 或提示词全文，也不同步到 Studio 项目或登记到 World。

本地准备、检查及可恢复技术修复自主执行。已有任务按 ID 查询，不因等待超时重新生成；新收费尝试限定在已授权次数或预算内。下载后验证文件、实际尺寸与服务端 SHA-256，完成 `image.rate_content` 分级评估后，`general` 产物按项目受控路径归档并保留原始产物。完成所请求的技术交付后返回候选及检查结果，艺术批准由用户或上层审核记录决定。
