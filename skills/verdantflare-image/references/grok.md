# Grok 图像调用

## 1. 发现与参数

通过 Studio MCP 发现 `image.capabilities`，确认 `engine=grok` 已启用并配置完整；模型取该能力的 `model`。新任务显式传真实 `project_id` 和固定 `idempotency_key`。

`image.create` 支持文生图，`resolution=1k/2k`；画幅使用能力声明内的比例。`image.edit` 只接受同一项目的一个 `source_artifact_id`，画幅继承源图。提示词保持用户意图。

不传 `quality`、`instructions`、遮罩、多参考、透明背景或 4K；不把 Grok 当作 Codex 或 Gemini 的同参数别名。现有 REST CLI 不支持该路径，直接使用已发现的 Studio MCP 工具。

## 2. 恢复与交付

保存任务 ID。`submission_unknown` 先核对原任务，不更换幂等键自动生成；`collect_retry` 仅恢复已保存结果。相同键和参数复用原任务，变更参数必须形成明确的新尝试。

下载后核对实际 MIME、宽高及 SHA-256，不按请求分辨率推算输出像素或插值冒充原生分辨率。凭据只存服务端 Secret，费用未返回时记为未知。按现行 Project/Artifact 协议保存候选，艺术审核由用户完成。
