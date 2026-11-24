---
name: verdantflare-artifact
description: 通过 VerdantFlare Studio Artifact MCP 上传、读取、下载并核验不可变内容版本。用于受控文件传输、ContentRef 完整性校验与中断上传恢复；不用于通用本地文件编辑或构建产物管理。
metadata:
  version: "0.1.0"
---

# VerdantFlare Artifact

## 1. 范围与边界

本技能通过宿主 Studio `/mcp` 的 `artifact.write` 与 `artifact.read` 管理不可变内容版本，覆盖已绑定 Studio 项目与独立 World 草稿的受控上传、读取、下载、ContentRef 完整性核验与中断恢复；不用于通用本地文件编辑或构建产物管理。

已绑定 Studio 项目中的创作、素材修改或查询自动完成必要上传或下载并沿用真实 `project_id`，写入任务继续交接 `verdantflare-project` 完成项目提交、业务关联与回读；明确仅本地、只讨论或只上传时遵循其范围。响应未知先对账恢复，不重复收费生成；同步不自动批准、选用或发布 World。本技能为仓库内技能，按需与 `verdantflare-project` 及 World 保存流程协同。

## 2. 能力清单与按任务读取

按要执行的原子能力直达对应入口：

| 原子能力（能干什么活） | 入口文档                                                          |
| :--------------------- | :---------------------------------------------------------------- |
| 受控传输               | [01.transfer.entrypoint.md](references/01.transfer.entrypoint.md) |

## 3. 工具与命令

优先使用宿主 Studio `/mcp` 发现并调用 `artifact.write` 与 `artifact.read`；无宿主工具时可使用 Studio 0.5.50 的 `studio-workspace tools` 与 `call --input request.json`（完整参数与恢复流程见能力入口）。

```text
studio-workspace tools
studio-workspace call --input request.json
```

环境变量使用 `STUDIO_MCP_URL` 与 `STUDIO_MCP_BEARER_TOKEN`，Bearer 由服务端解析真实身份，不额外登录或提交用户/组织头。
