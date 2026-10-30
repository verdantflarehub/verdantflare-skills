# MOSS-Image 自托管引擎调用说明

## 1. 当前依据

- [调用契约](contracts.md)：Studio Image MCP 工具发现、任务轮询与 Artifact 归档。
- [生成后内容分级评估步骤](content-rating-step.md)：MOSS-Image 产物生成后的 18+ 评估与 `image.rate_content` 标记。
- [MOSS-Image 主设计](../../../../docs/design/app/image/moss-image.md)：已批准的一期能力与参考图限制。
- [多参考图参数契约（待 H1）](../../../../docs/design/app/image/moss-image.md#167-多参考图参数契约待-h1)：新模式的唯一设计依据，尚未批准或交付，不作为当前调用能力。

## 2. 引擎定位与能力发现

`moss-image` 是 Image 应用的自托管文生图与多模态参考生图引擎（基于 `Comfy-Org/MOSS-Image` INT8-ConvRot 权重、`qwen3.5_9b_int8_convrot` 多模态编码器与 `flux2-vae_small`），主打中英文密集文字渲染（海报、包装、信息图）与原生 JSON 版式控制；基于 `Qwen3.5-9B` 视觉编码的参考图条件生成（R2I）为实验性能力。

- **不保证人物一致**：官方模型未以参考图训练，现行 R2I 只能传递构图、服装等粗粒度信息，五官与脸型会明显偏离参考图。要求人物身份一致时，选择用户已授权且实际可用的 Google 或 OpenAI 渠道；用户明确指定 MOSS 时说明能力限制，不自动换用收费渠道。确需 MOSS 的文字与版式效果且任务已授权换脸时，可先出图，再使用实际发现的 `image.faceswap`，逐张检查结果。

- **统一入口**：先发现真实 Studio MCP 工具，优先 `image.create` / `image.edit`（现行 MOSS 编辑入口仍为参考图条件生成），仅在实际发现 `image.generate` 时使用该兼容入口；传入 `engine: "moss-image"`。底层 `image-moss-api` 仅在集群内受控可达，客户端不直连，也不调用内部 Edit 实验接口。
- **能力核验**：调用前通过 `image.capabilities` 核验 `engine="moss-image"` 已声明、支持 `rate_content` 且尺寸、参考图与版式规则匹配。
- **非渠道引擎**：`moss-image` 是自托管 GPU 引擎，不经过渠道应用，没有上游 Key 或美元预算。

## 3. 请求参数与拒绝边界

### 3.1 支持的参数

以下为现行调用摘要，具体工具接受哪些字段以本次发现的 schema 为准；不能把 `image.create` 的参数集合直接用于 `image.edit`。

- **`project_id` / `idempotency_key`**：必填，使用真实 Studio 项目 ID 与固定幂等键。
- **`engine`**：固定传 `"moss-image"`。
- **`prompt`**：现行服务要求非空、最多 4000 字符，`layout` 不能替代 prompt；同时提供时由服务合并。不要按旧文档假定已有 4096 token 分词校验。
- **`layout`**（可选，实验性）：沿用现行 `meta` 与 `items[{id,desc,box_2d}]` 格式，坐标为 `[x1,y1,x2,y2]`，同一请求使用归一化坐标或 0～1000 整数坐标；上限 32 项，详见[主设计第 7 节](../../../../docs/design/app/image/moss-image.md#7-版式-json-校验)。不要发送旧文档中的 `summary.caption` / `bbox` 格式。
- **`width` / `height` 与 `aspect_ratio` / `resolution`**：宽高必须为 16 的正整数倍，范围 `[512, 2048]`，总像素不超过 `2,097,152`（2M）；原生支持 `1:1`、`2:3`、`3:2`、`3:4`、`4:3`、`9:16`、`16:9` 画幅与 `1k` / `2k` 分辨率档位。工具默认值可能不同；需要 `1024×1024` 时显式指定受支持的宽高或 `1:1` / `1k`，不从内部引擎默认值推断公共入口输出。
- **`steps` / `cfg_scale` / `seed`**：默认采用官方 50 步、CFG `3.0`、Euler、shift `5`（跳第 22 层）；`seed` 省略时由服务端生成并写入结果。现行调用使用实际 schema 声明的 `cfg_scale`；`guidance` 的别名映射属于待审修正，不能仅凭函数接收字段就假定已参与推理。
- **`negative_prompt`**：可选，默认为空；`moss-image` 有独立负向分支，不套用 Codex 通道的负向词剥离清洗。
- **`reference_artifact_ids` / `reference_images` / `source_artifact_id` / `face_reference_artifact_id` / `source_identity_artifact_id`**（可选，R2I）：支持传入 1～3 张参考图 Artifact ID，由服务端读取参考图字节并通过 `TextEncodeQwenImage21`（`resolution=384`）作为实验性语义提示送入 `Qwen3.5-9B` 编码器；不锁定人物身份，见第 2 节。

### 3.2 显式拒绝的参数

`moss-image` 不支持局部蒙版重绘（`inpaint`）、4K 超分或渠道计费参数。以下参数或操作一律返回 `INVALID_ARGUMENT`，调用时不得传入：

- `mask`、`mask_artifact_id`、`image.inpaint`、`action="inpaint"`
- `prefer_4k`、`resolution="4k"`、`channel_account_id`、`budget_limit`

### 3.3 多参考图模式摘要（待 H1，不是当前调用能力）

以下只帮助理解[主设计的多参考图参数契约](../../../../docs/design/app/image/moss-image.md#167-多参考图参数契约待-h1)。须 H1 批准、完成 T3/T4 实现验证且实际工具及能力发现包含对应模式后，才按该契约构造请求；本技能不据草案试探未公开参数。

- 保留同一 `moss-image` 引擎与 `moss-image-sft-int8-convrot` 模型，`moss_mode` 省略或为 `semantic` 时保持旧行为，不自动换脸。
- `identity` 是推荐的路线 A，入口为 `image.create` / 实际发现的 `image.generate`；使用人物 `source_identity_artifact_id` 和服装 `outfit_reference_artifact_id` 两图生成，随后换脸。
- `edit` 是路线 B，入口为 `image.edit`；`source_artifact_id` 明确为被编辑原图，另有服装 `outfit_reference_artifact_id`，用于保持构图的编辑。
- `edit_identity` 是显式路线 A+B，入口为 `image.edit`；MOSS 只接收原图和服装两图，另传的人物 `source_identity_artifact_id` 只供换脸使用。
- 身份兼容别名为 `face_reference_artifact_id`，与规范字段同时出现但值不同则拒绝。新结构化模式不混用 `reference_artifact_ids` / `reference_images`，人物/原图与服装必须不同，edit_identity 的身份图可等于原图但不能等于服装图；所有输入使用同项目原生 Image Artifact，不传中央 ContentRef、URL 或磁盘路径。
- `edit_lora_strength` 仅用于 `edit` / `edit_identity`，范围 `0..2`、默认 `1`；参考图缩放由服务按输出长边固定处理，不向工具发送内部 `reference_max_edge`。`layout` 只适用于 `semantic` / `identity`。

新安装的正式发行配置拟默认关闭 Edit；现有 dev 实验显式开启不代表公共入口已提供该能力。权重许可由用户在正式发布前确认，不由技能向外询问；路线 C 训练不在调用流程中自动启动。

## 4. GPU 租约、超时与恢复

- **按需启停与租约**：`moss-image` 使用按需启停租约 `gpu-task-moss-image`（空闲 10 分钟缩回 0，等待 Music3 空闲）。冷启动或排队时状态可能返回 `ENGINE_NOT_READY` 或 `queued`，继续按原 `task_id` 轮询，不换键重提。
- **超时上限**：单任务推理超时上限为 `300 s`（含冷启动等待最高 `600 s`）。
- **重启与恢复**：调用方保留原 `task_id` 与幂等键，先查询并核对产物；超时、重启或回到排队状态均不是新建任务的依据。现行内部推理是同步接口，不能假定存在 `/v1/jobs` 或可查询的引擎作业；旧任务的自动恢复行为尚有实现与设计差异，无法确认结果时记录待核对，禁止自动重提。
- **待审组合模式的失败**：全部阶段成功才交付最终图；换脸失败时保留中间图与拒绝原因，但中间图不能冒充完整结果。内容或姿态拒绝不得通过改检查阈值、跳过换脸或换云渠道绕过；结果未知时核对原阶段记录，不自动重新生成。具体阶段恢复按主设计及发布后的实际能力执行。

## 5. 生成后分级评估（K09）

取得 `moss-image` 生成结果后，必须先按 [生成后内容分级评估步骤](content-rating-step.md) 与 [18+ 评估规范（rubric v1.0）](content-rating-rubric.md) 完成图像、正向提示词与版式文字的 18+ 评估，并调用 `image.rate_content` 提交结果（`general` 与 `nsfw` 均提交），再决定交付摘要展示与 Project 同步。
