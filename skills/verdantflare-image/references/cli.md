# Image CLI客户端说明

附带 `scripts/image_client.py` 已适配 Studio `/mcp` JSON-RPC 网关与兼容 REST 模式：当 `STUDIO_MCP_URL` 以 `/mcp` 结尾时，客户端自动通过 JSON-RPC `tools/call` 调用 `image.import`（或分片导入 `image.import_prepare` / `import_chunk` / `import_commit`）、`image.create`、`image.edit`、`image.status`、`image.result` 与 `image.rate_content`，并通过 Studio 受控下载路由 `/apps/image/artifacts/{artifact_id}/content` 下载产物并校验 SHA-256。

Project/Artifact 中央工程文件同步配合 [统一客户端](../../_shared/project-world-client.md)（`studio-workspace`）使用，连接与身份问题见 [环境说明](../../ENVIRONMENT.md)。

## 1. 现有命令与用法

```text
python3 scripts/image_client.py --help
python3 scripts/image_client.py generate --help
```

| 命令 | 当前实现与使用边界 |
| :--- | :--- |
| `generate` | Studio `/mcp` JSON-RPC（`image.import` + `image.create`/`image.edit` + `image.status` + `image.result` + `/apps/image/artifacts/.../content` 下载 + 可选 `image.rate_content`）及兼容 REST；必须显式传入 `--project-id`（UUIDv7）与 `--idempotency-key`，支持 `--rate-rating general\|nsfw` 自动完成 K09 分级评估 |
| `stats` | Studio `/mcp` 下调用 `image.capabilities`，REST 下查询 `/api/tasks/stats`；用于检查当前网关支持的渠道、模型与预算规则 |
| `status` | Studio `/mcp` 下调用 `image.status`（完成时附带 `image.result`）；使用原 `task_id`，查询失败不触发重新生成 |
| `download` | Studio `/mcp` 下解析为 `<studio_root>/apps/image/artifacts/{artifact_id}/content` 并校验 SHA-256；自动剥离 `/mcp` 后缀，避免拼接错误路径 |
| `rate` | 调用 `image.rate_content` 提交 `general` / `nsfw` 分级评估（`rubric v1.0`）；幂等追加标记，不提供撤销 |

调用失败应分别记录客户端地址构造、认证和服务响应。没有收到任务ID时核查同一幂等键的状态，不自行换键重放；已取得ID则用宿主 `image.status/result` 恢复。

后续CLI适配应复用Studio工具发现、真实会话和受控传输，并验证提交幂等、任务恢复、项目归属与内容哈希；在实现及检查完成前，不把文档修正标为客户端已修复。
