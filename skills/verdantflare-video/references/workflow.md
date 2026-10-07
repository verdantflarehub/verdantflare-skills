# H3单元执行与恢复

## 提交前

1. 确认真实Studio会话，发现当前创建、状态、结果工具。现有网关为 `video.create/status/result`；管理工具缺失或403先按 [环境说明](../../ENVIRONMENT.md) 排查旧共享令牌。
2. 创建或打开当前Project，读取已保存的Generation Unit。核实模型、当前渠道配置与就绪状态、时长、画幅和输入模式；不从旧文档猜默认渠道。
3. 按 [调用说明](video-mcp.md) 将授权的本地/World输入归档，并适配为Video可读取的同项目引用。只拿到中央ContentRef或Image原生ID时，不能声称Video输入已经齐备。
4. 固定Prompt和引用顺序，计算规范化输入摘要。相同单元版本的不同输入应新建版本，不能覆盖旧摘要。
5. 保存不可变 `attempt_id`、幂等键及请求；准备完成后才进入提交阶段。工具或引用适配缺失时不直连Runtime，独立本地工作继续。

## 提交与恢复

调用当前注册的创建工具前将Attempt置为 `submitting`。成功返回后先保存原始任务ID，再查询状态。未收到明确提交结果时记录 `submission_unknown`，按原幂等键/任务ID恢复；不能将网络超时猜成失败，也不能换渠道或新建幂等键重放。

- `queued` / `running`：保存最近状态，继续查询原任务。
- `succeeded`：读取结果，进入媒体验证。
- `failed` / `cancelled`：保存错误或取消状态，结束本次Attempt。
- 未知状态：保留原始值并核查协议，不映射成成功或已确认失败。

当前服务若没有按幂等键查询不确定提交的能力，保留不确定状态并报告恢复缺口。不要写成“未创建任务”。

## 现有Video CLI边界

`scripts/video_client.py` 的命令名与实际分支不同：

| 命令 / 选择 | 当前行为 |
| --- | --- |
| `check` | 检查旧工具集合 `artifact.import`、`video.generate/status/result`；对只注册 `video.create` 的网关可能误报不兼容 |
| `generate --model minimax-h3-ref2va --route fal` | 旧MCP fal分支，调用 `video.generate`；只有与实际工具匹配时才可用 |
| `import` | 调用旧 `artifact.import`；不是中央 `artifact.write` 或引用适配的替代品 |
| `status` / `result` | 查询原Video任务，仍需确认认证、任务参数与返回格式兼容 |
| `resume` | 按路由或任务ID识别MCP/旧SD2记录；不能用它改变原任务渠道 |
| 其他生成、`list` / `recover` | 仍有旧SD2公共API、本地记录和S3流程，不是通用H3入口 |

当前新Studio任务优先使用宿主MCP工具，不能只因CLI `check` 失败就报告服务不可用。没有选择fal的 `generate` 命令不能当作默认H3调用；也不为连接Studio运行旧SD2安装器。

## 结果、归档与重试

按 [媒体校验](validation.md) 完成下载、完整解码、摘要核验、逐秒联系表与完整回放。媒体技术可用不代表身份、表演和摄影连续性通过。

保存单元版本、Attempt、输入摘要、幂等键、原任务ID、原生Artifact、实际渠道/Runtime及检查结果。通过宿主适配或现有工作副本客户端归档当前Project；保留原生来源映射，不把原生任务完成等同中央提交完成。结果为候选，艺术批准与选用单独记录。

- 下载和本地校验故障：重取同一结果，修复本地问题，不重新生成。
- 明确生成失败且授权预算允许重做：保留旧Attempt，创建新Attempt。
- Prompt、引用、时长、画幅或模型变化：新建单元版本，不复用旧幂等键。
- 创作检查失败：记录具体缺陷，在授权范围内修订候选，不伪造人工通过。
- 取消只作用于明确任务，不删除原始Artifact或其他输出。
