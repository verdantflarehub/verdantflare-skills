# Image CLI兼容说明

附带 `scripts/image_client.py` 是现存REST客户端，不是Studio JSON-RPC客户端。它读取Studio配置变量后仍拼接 `/api/tasks`、`/api/tasks/stats` 和Artifact下载路径；把 `https://studio.example.com/mcp` 传给它会形成错误REST路径。

当前Studio流程优先使用宿主实际注册的 `image.create/edit/inpaint/status/result`。Project/Artifact文件操作使用 [统一客户端](../../_shared/project-world-client.md)，连接与身份问题见 [环境说明](../../ENVIRONMENT.md)。不能以恢复旧CLI为由直连下游服务或供应商。

## 现有命令与限制

```text
python3 scripts/image_client.py --help
python3 scripts/image_client.py generate --help
```

这些帮助命令仅用于核对本地实现，不创建任务。

| 命令 | 当前实现 | 使用边界 |
| --- | --- | --- |
| `generate` | REST提交、轮询、下载 | 需要 `--project-id`；尚未完成Studio网关适配，不作为本页的新任务示例 |
| `stats` / `list` | REST统计与列表 | 不是对同名MCP工具存在性的证明 |
| `status` | REST任务查询 | 使用原任务ID；失败不能触发重新生成 |
| `download` | REST Artifact下载及哈希校验 | 需要正确的宿主传输与鉴权适配，不能拼接到 `/mcp` 后 |

调用失败应分别记录客户端地址构造、认证和服务响应。没有收到任务ID时核查同一幂等键的状态，不自行换键重放；已取得ID则用宿主 `image.status/result` 恢复。

后续CLI适配应复用Studio工具发现、真实会话和受控传输，并验证提交幂等、任务恢复、项目归属与内容哈希；在实现及检查完成前，不把文档修正标为客户端已修复。
