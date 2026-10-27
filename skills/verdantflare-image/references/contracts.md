# Image经Studio调用说明

本页说明Skill需要的调用语义，不替代宿主实际工具Schema或中央Project/Artifact契约。开始时使用真实Studio会话执行 `tools/list`，再选择已注册工具；认证见 [环境说明](../../ENVIRONMENT.md)。

## 工具名与必需上下文

| 操作 | 现有网关注册名 | 说明 |
| --- | --- | --- |
| 文生图 | `image.create` | 传Prompt、项目、幂等键及已支持的图像参数 |
| 编辑 | `image.edit` | 主参考 `source_artifact_id`，可选附加参考列表 |
| 局部重绘 | `image.inpaint` | 主参考与遮罩引用；形状和尺寸按当前契约核对 |
| 状态 | `image.status` | 用原始 `task_id` 查询 |
| 结果 | `image.result` | 完成后取得原生Artifact、摘要及下载信息 |
| 内容分级标记 | `image.rate_content` | 生成后提交 `general` / `nsfw` 评估 JSON（只加标记、不提供撤销） |
| 导入输入图 | `image.import`；`image.import_prepare` / `import_chunk` / `import_status` / `import_commit` | 把本地或渲染图片写成同项目原生 Image Artifact；单次不超过 3 MiB，分片每片不超过 512 KiB，单张不超过 20 MiB |

历史 `image.generate`、`image.list` 仅在宿主实际注册并明确语义时使用。当前不假定存在 `artifact.import`，也不向网关发送文档中未注册的别名。

即使旧Schema把字段标为可选，制作任务仍显式提供服务生成的 `project_id` 和固定 `idempotency_key`。样例中的标识只解释字段，不能直接提交：

```json
{
  "project_id": "<当前Studio项目ID>",
  "idempotency_key": "B01/wardrobe-v1/attempt-01",
  "prompt": "A single full-body wardrobe reference on a plain neutral background.",
  "engine": "codex",
  "model": "gpt-image-2.5-sunburst",
  "aspect_ratio": "16:9",
  "resolution": "2k",
  "quality": "high",
  "background": "opaque"
}
```

这是 `image.create` 的示意参数；可选字段只在当前实现支持时传入。`resolution` 是请求规格，不能证明返回图片已经达到该尺寸；下载后解码核验。`quality` 使用宿主支持的枚举，不把历史 `hd`、`xhigh`、`max` 宣称为所有模型的通用值。

选择 Google 渠道（`engine="gemini"`）时，`model` 传真实模型编码（默认 `"gemini-nano-banana-2.1"`，另可选 `"gemini-3-pro-image"`、`"gemini-3.1-flash-image"`、`"gemini-3.1-flash-lite-image"`、`"gemini-2.5-flash-image"`），画幅支持 `1:1`、`2:3`、`3:2`、`3:4`、`4:3`、`9:16`、`16:9`，可按需传 `channel_account_id` 与 `budget_limit`，详见 [Google 图像调用](google.md)。

编辑在相同项目、幂等上下文下传入 `source_artifact_id`、可选 `reference_artifact_ids` 和编辑Prompt。说明每张图保留面容、服装、场景中的哪些内容，明确允许修改什么。`image.edit` 与 `image.create` 的尺寸字段可能不同，不能把 `aspect_ratio`、`resolution`、`size` 无条件互换；从当前Schema或匹配部署版本的接口说明确认。

## 中央素材与Image原生引用

中央Artifact以 `ContentRef = store_id + artifact_id + version_id` 固定版本。现存Image服务可能使用自己的原生Artifact ID；两者不是同一引用格式，不能只取中央的 `artifact_id` 填入 `source_artifact_id`。

1. 本地原图通过 `studio-workspace import` 保存到当前Project；远端/World文件先核验权限与固定版本。新内容走中央Artifact，不让客户端直接写S3。
2. 用 Image MCP 导入工具获得 Image 可读取的原生引用：PNG、JPEG、WebP，单张不超过 20 MiB、50 MP。不超过 3 MiB 用 `image.import`（`project_id`、`filename`、`expected_sha256`、`content_base64`）；更大的用 `image.import_prepare`（固定 `idempotency_key`、`size`、`sha256`、`purpose`，可选 `source_content_ref` 只作来源登记）后按返回的 `offset` 逐片 `image.import_chunk`，最后 `image.import_commit`。中断时用 `image.import_status` 读取已确认偏移续传，不换键重来。每次调用检查项目写权限；扩展名须与实际格式一致。浏览器上传入口 `POST /apps/image/api/artifacts/upload` 只认登录会话，MCP 客户端不使用。
3. 兼容导入后记录中央ContentRef、Image原生ID、文件大小和SHA-256映射。若传输限制要求制作压缩副本，保留原图，副本单独命名、校验并登记，不能把副本哈希写成原图哈希。
4. 只有HTTPS导入时，先确认宿主实际提供该工具及来源许可。受控下载路径不是匿名公网URL；禁止把会话令牌放入URL，或为了导入把私有素材公开上传。

## 状态、结果与下载

创建成功后先保存 `task_id` 再轮询。Image原生状态通常为 `queued`、`running`、`completed`、`failed`、`canceled`；未知状态按协议差异处理，不猜成失败。

`image.result` 可返回 `artifact_id`、`project_id`、`filename`、`sha256`、`size_bytes`、`metadata`、`download_path`。这些属于原生结果，不代表中央Project已经提交。SHA-256、字节数和实际尺寸都需核验，Prompt和模型返回的元数据不能代替文件检查。

下载通过Studio受控内容路由。现有Image宿主适配为 `/apps/image/artifacts/{artifact_id}/content`；中央文件则使用 `artifact.read(mode=download)` 返回的内容路径或工作副本 `fetch`。按当前宿主声明解析原生路径，不能把 `/image/...` 随意拼到 `/mcp` 后，也不能把Studio凭据转发给任意外域。

## Project归档

有中央原生输出适配时，使用其可信来源映射提交文件。没有该适配时，授权范围内可将已下载、校验的结果通过现有工作副本客户端显式导入，来源为 `user_import`，保留原任务ID与原生Artifact映射；不伪造 `task_output`。

通过 `project.commit` 保存文件与 `run_refs`，Prompt和审核说明保存为MD/TXT。`domain_documents` 只登记服务支持的领域JSON，审核MD作为普通文件或入口。候选不自动进入 `selections`，更不自动注册World；只有实际选用或授权复用时才登记对应关系。
