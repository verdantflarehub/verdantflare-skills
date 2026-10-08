# H3单元执行与恢复

## 提交前

1. 确认真实Studio会话，发现当前创建、状态、结果工具。现有网关为 `video.create/status/result`；管理工具缺失或403先按 [环境说明](../../ENVIRONMENT.md) 排查旧共享令牌。
2. 创建或打开当前Project，读取已保存的Generation Unit。核实模型、当前渠道配置与就绪状态、时长、画幅和输入模式；不从旧文档猜默认渠道。
3. 按 [调用说明](video-mcp.md) 将授权的本地/World输入归档，并适配为Video可读取的同项目引用。只拿到中央ContentRef或Image原生ID时，不能声称Video输入已经齐备。
4. Singularity 调用 `video.preflight` 确认实际输出网格与参考编号，再固定 Prompt、引用顺序和输入摘要。相同单元版本的不同输入应新建版本，不能覆盖旧摘要。
5. 保存不可变 `attempt_id`、幂等键及请求；准备完成后才进入提交阶段。工具或引用适配缺失时不直连Runtime，独立本地工作继续。

## 提交与恢复

调用当前注册的创建工具前将Attempt置为 `submitting`。成功返回后先保存原始任务ID，再查询状态。未收到明确提交结果时记录 `submission_unknown`，按原幂等键/任务ID恢复；不能将网络超时猜成失败，也不能换渠道或新建幂等键重放。

- `queued` / `running`：保存最近状态，继续查询原任务。
- `succeeded`：读取结果，进入媒体验证。
- `failed` / `cancelled`：保存错误或取消状态，结束本次Attempt。
- 未知状态：保留原始值并核查协议，不映射成成功或已确认失败。

当前服务若没有按幂等键查询不确定提交的能力，保留不确定状态并报告恢复缺口。不要写成“未创建任务”。

## 现有Video CLI边界

`scripts/video_client.py` 的调用边界：

- `check`：分页发现工具，优先 `video.create`；读取 capabilities 的宿主默认渠道和适配器限制，不提交生成，不把 configured 当就绪。
- `generate`：默认 H3，经 Studio 创建；显式 route 优先，否则解析宿主默认。Singularity 自动预检并把结果保存在 Attempt，不静默改画幅或换渠道。
- `import`：本地 `--file` 或中央 `--content-ref-file` 优先走分块协议，支持图像、视频和音频；相同来源、文件名、用途、摘要生成固定导入键。中断后重跑同一命令查询并续传，commit 丢失响应时复用同一导入身份。`--purpose` 记录用途，允许来源 `--source-url` 仍走兼容入口。
- `status` / `result`：查询原 Video 任务，确认返回格式兼容。
- `resume`：按路由或任务 ID 识别 MCP／旧 SD2 记录，不能改变原渠道。
- `list` / `recover`：历史 SD2 查询恢复入口。

旧宿主没有 `video.capabilities` 时须显式指定已核实渠道；没有分块工具时只保留原 3 MiB 图片兼容入口，大图和音视频报告接入版本缺口。新 SD2 由宿主工具处理，不进入历史直连分支。

中央引用文件保存 `{"content_ref":{"store_id":"<固定ID>","artifact_id":"<固定ID>","version_id":"<固定ID>"}}`；经Project/World读取时另附完整 `access`（项目ID+修订ID或资产ID+版本ID）。所有ID使用服务返回的UUIDv7。示例：

```sh
python scripts/video_client.py check
python scripts/video_client.py import --project-id <项目ID> --content-ref-file reference.json --filename reference.png --sha256 <固定摘要>
python scripts/video_client.py generate --project-id <项目ID> --idempotency-key unit_v1/attempt_01 --route <已核实渠道> --prompt "<已保存提示词>" --duration 15 --ratio 16:9 --image-ref <返回的Video原生ID>=identity
```

最后一条仅在所选适配器实际接受15秒横屏时执行。它表达制作目标，不是已成功生成的证据。导入映射保存于本地状态目录 `mcp-imports/`，供归档Project时登记；不能把本地映射文件当成已提交的项目修订。

## 结果、归档与重试

按 [媒体校验](validation.md) 完成下载、完整解码、摘要核验、逐秒联系表与完整回放。媒体技术可用不代表身份、表演和摄影连续性通过。

保存单元版本、Attempt、输入摘要、幂等键、原任务ID、原生Artifact、实际渠道/Runtime及检查结果。通过宿主适配或现有工作副本客户端归档当前Project；保留原生来源映射，不把原生任务完成等同中央提交完成。结果为候选，艺术批准与选用单独记录。

- 下载和本地校验故障：重取同一结果，修复本地问题，不重新生成。
- 明确生成失败且授权预算允许重做：保留旧Attempt，创建新Attempt。
- Prompt、引用、时长、画幅或模型变化：新建单元版本，不复用旧幂等键。
- 创作检查失败：记录具体缺陷，在授权范围内修订候选，不伪造人工通过。
- 取消只作用于明确任务，不删除原始Artifact或其他输出。

汇报分别标明代码/Git完成、素材接入、任务ID与状态、结果下载校验、人工批准。仅参考图完成或代码提交时，不写“视频制作完成”。
