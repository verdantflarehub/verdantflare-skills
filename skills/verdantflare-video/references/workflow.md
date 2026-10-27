# H3单元执行与恢复

## 1. 提交前

1. 确认真实Studio会话，发现当前创建、状态、结果工具。现有网关为 `video.create/status/result`；管理工具缺失或403先按 [环境说明](https://github.com/verdantflarehub/verdantflare-skills/blob/verdantflare-video-v0.4.0/skills/ENVIRONMENT.md) 排查旧共享令牌。
2. 创建或打开当前Project，读取已保存的Generation Unit。核实模型、当前渠道配置与就绪状态、时长、画幅和输入模式；不从旧文档猜默认渠道。
3. 按 [调用说明](video-mcp.md) 将授权的本地/World输入归档，并适配为Video可读取的同项目引用。只拿到中央ContentRef或Image原生ID时，不能声称Video输入已经齐备。
4. 实际注册 `video.preflight` 时调用并确认参数、素材和报价；Singularity 另核对实际输出网格与参考编号。`can_submit=false` 保留具体原因并停止。再固定 Prompt、引用顺序和输入摘要；相同单元版本的不同输入应新建版本，不能覆盖旧摘要。
5. 保存不可变 `attempt_id`、幂等键及请求；准备完成后才进入提交阶段。工具或引用适配缺失时不直连Runtime，独立本地工作继续。

## 2. 提交与恢复

调用当前注册的创建工具前将Attempt置为 `submitting`。成功返回后先保存原始任务ID，再查询状态。未收到明确提交结果时记录 `submission_unknown`，按原幂等键/任务ID恢复；不能将网络超时猜成失败，也不能换渠道或新建幂等键重放。

- `queued` / `running`：保存最近状态，继续查询原任务。
- `succeeded`：读取结果，进入媒体验证。
- `failed` / `cancelled`：保存错误或取消状态，结束本次Attempt。
- 未知状态：保留原始值并核查协议，不映射成成功或已确认失败。

当前服务若没有按幂等键查询不确定提交的能力，保留不确定状态并报告恢复缺口。不要写成“未创建任务”。

## 3. 现有Video CLI边界

`scripts/video_client.py` 的调用边界：

- `check`：分页发现工具，优先 `video.create`；读取 capabilities 的宿主默认渠道和适配器限制，不提交生成，不把 configured 当就绪。
- `generate`：经 Studio 创建，显式模型／渠道优先，否则解析宿主明确默认；精确匹配能力行和工具 Schema。模型、模式、画幅、时长和分辨率不使用固定白名单；默认值只取已声明能力或匹配工具 Schema，声明不一致就停止。已注册预检时自动调用并保存结果，不静默改画幅或换渠道。
- `import`：本地 `--file` 或中央 `--content-ref-file` 优先走分块协议，支持图像、视频和音频；相同来源、文件名、用途、摘要生成固定导入键。中断后重跑同一命令查询并续传，commit 丢失响应时复用同一导入身份。`--purpose` 记录用途，允许来源 `--source-url` 仍走兼容入口。
- `status` / `result`：查询原 Video 任务，确认返回格式兼容。
- `resume`：按路由或任务 ID 识别 MCP／旧 SD2 记录，不能改变原渠道。
- `list` / `recover`：历史 SD2 查询恢复入口。

旧宿主没有 `video.capabilities` 或创建输入 Schema 时停止新生成，状态／结果恢复仍使用原任务。没有分块工具时只保留原 3 MiB 图片兼容入口，大图和音视频报告接入版本缺口。新 SD2 通过动态能力匹配的 Studio 分支处理，不进入历史直连分支。

可显式传 `--model`、`--mode`、`--resolution`、`--ratio`、`--duration`；参考重复传 `--image-ref`／`--video-ref`／`--audio-ref ARTIFACT_ID=PURPOSE`。`--budget-limit` 是服务声明币种中的正数预算上限，`--channel-account-id` 选择已有授权账号；只有实际创建及预检 Schema 声明这些字段时才传递。Higgsfield 的 `--price-ceiling` 单独表示 Credits 上限，不与其他渠道币种预算换算。预算不是估价或实际结算，不因未知价格而填零。图片、音视频数量由匹配能力行约束，文件格式、字节、时长与像素的最终校验由服务端导入和模型预检执行。

CLI 的 MCP 失败输出结构化 `error`，保留 `code`、安全 `message`、`source`、`retryable` 与可用的 `accepted`／领域任务 ID。`accepted=false` 保存为拒绝；没有明确受理状态仍保存为未知。`retryable=true` 不授权再次 create。

中央引用文件保存 `{"content_ref":{"store_id":"<固定ID>","artifact_id":"<固定ID>","version_id":"<固定ID>"}}`；经Project/World读取时另附完整 `access`（项目ID+修订ID或资产ID+版本ID）。所有ID使用服务返回的UUIDv7。示例：

```sh
python scripts/video_client.py check
python scripts/video_client.py import --project-id <项目ID> --content-ref-file reference.json --filename reference.png --sha256 <固定摘要>
python scripts/video_client.py generate --project-id <项目ID> --idempotency-key unit_v1/attempt_01 --route <已核实渠道> --prompt "<已保存提示词>" --duration 15 --ratio 16:9 --image-ref <返回的Video原生ID>=identity
```

最后一条仅在所选适配器实际接受15秒横屏时执行。它表达制作目标，不是已成功生成的证据。导入映射保存于本地状态目录 `mcp-imports/`，供归档Project时登记；不能把本地映射文件当成已提交的项目修订。

## 4. 结果、分级评估、归档与重试

按 [媒体校验](validation.md) 完成下载、完整解码、摘要核验、逐秒联系表与完整回放。媒体技术可用不代表身份、表演和摄影连续性通过。

**生成后内容分级评估（K09 强制步骤）**：下载校验完成后、在交付摘要展示与 Project 归档前，必须按 [生成后内容分级评估步骤](content-rating-step.md) 与 [18+ 评估规范（rubric v1.0）](content-rating-rubric.md) 在本机临时目录抽帧（至少每 2 秒 1 帧、全片至少 8 帧，另补镜头切换处关键帧；评估后删除临时帧文件，并在 `reasons` 中记录抽帧数量），结合连续帧序列、音轨、正向提示词与标题判定，调用 `video.rate_content` 提交分级结果（`general` 与 `nsfw` 均须提交；超时用相同内容幂等重试，不重新生成）。若判为 `nsfw`，交付摘要只写“N 个产物已标记为 NSFW 并隐藏”，不展示预览、联系表、`ContentRef` 或提示词全文，且不同步到 Studio 项目或登记到 World。

对判定为 `general` 的产物，保存单元版本、Attempt、输入摘要、幂等键、原任务ID、原生Artifact、实际渠道/Runtime及检查结果。通过宿主适配或现有工作副本客户端归档当前Project；保留原生来源映射，不把原生任务完成等同中央提交完成。结果为候选，艺术批准与选用单独记录。

- 下载和本地校验故障：重取同一结果，修复本地问题，不重新生成。
- 明确生成失败且授权预算允许重做：保留旧Attempt，创建新Attempt。
- Prompt、引用、时长、画幅或模型变化：新建单元版本，不复用旧幂等键。
- 创作检查失败：记录具体缺陷，在授权范围内修订候选，不伪造人工通过。
- 取消只作用于明确任务，不删除原始Artifact或其他输出。

汇报分别标明代码/Git完成、素材接入、任务ID与状态、结果下载校验、人工批准。仅参考图完成或代码提交时，不写“视频制作完成”。

## 5. 任务 Title

已有 Agent 在创作上下文生成或沿用 Title，CLI 生成可加 `--title "湖面漂流的纸船"`。Title 是命名快照，与固定 Generation Unit / Attempt 的生成 request 分离；原 project_id、幂等键、task ID 和输入摘要仍是恢复身份。resume/status/result 读取原任务，不以重复名称查找、重命名或重新生成；用户报告同名时附镜头上下文或时间。具体字段及旧宿主兼容见 [MCP 调用说明](video-mcp.md)。
