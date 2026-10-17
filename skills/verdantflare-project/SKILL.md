---
name: verdantflare-project
description: Create, open, save and recover VerdantFlare Studio projects through Studio MCP. Use automatically for material creation, edits, queries and deletions in an already bound Studio creative project, including transfer and association checks; not for generic software planning or Git repositories.
---

# VerdantFlare Project

已绑定 Studio 项目的创作任务默认完成素材增删改查所需的传输、项目提交与角色关联核验，沿用真实 project_id；用户无需另说上传、下载、同步或检查网站。仅本地、只讨论或暂不上传的明确限定优先。只上传或本地保存不算已同步，响应未知先对账恢复，不重复收费生成。同步不自动批准、选用或发布 World。技能负责助手任务期间的收尾，不承诺无人运行时的文件监控。

## 1. 连接与范围

通过宿主 Studio `/mcp` 管理工程。沿用 `STUDIO_MCP_URL` 和 `STUDIO_MCP_BEARER_TOKEN`；Bearer 代表真实用户，不增加登录、密码或用户/组织请求头。
先发现当前工具，确认本次任务需要的能力及参数。工具可见不代表有项目写权限。

优先使用宿主 MCP。需要本地文件读写时使用 `studio-workspace`，操作见[工作副本](references/workspace.md)。
直接提交、固定资产引用或恢复未知结果时，读取[修订与恢复](references/revisions.md)。两种方式使用同一用户凭据。

MCP Token 即用户调用授权，由 Studio 解析为真实用户并按其业务权限执行，不增加独立审批、再次登录或密码确认。媒体调用与未知提交恢复见[MCP 授权与恢复](references/mcp-authority.md)。

## 2. 打开与保存

1. 先读取当前目录客户端维护的 `.vf/project.json` 与 `.vf/local.json`，核对 project_id、连接别名与基线；已有绑定就使用该项目，不另行猜选或新建。两者不一致时保留原状并核实，不手写修复。无绑定时沿用用户明确的项目，或通过 `project.list` 定位；存在无法区分的多个候选才询问。仅任务确需新项目时用 `project.create` 并采用返回 ID。读取失败、服务不可用或工具发现失败不能作为新建项目或更换绑定的理由。
2. 用 `project.open` 固定基线修订，区分所选 `revision_id` 与当前 `head_revision_id`。按需取文件，保护本地修改；没有下载的文件不是删除指令。
3. 明确本次修改的文本、文件、选用与任务引用。小文本可直接提交；二进制先经 Artifact 获得 ContentRef。AI 短剧素材按[修订与恢复](references/revisions.md)核对对象、状态与制作版本关联；仅添加文件不代表已在对应素材页归位。不要扫描整目录替用户选择成果。
4. 发送前持久保存原请求及稳定 `commit_id`。用 `expected_revision_id` 提交，正文和相关选用在同一修订更新。请求文件不含凭据。
5. 回读返回的固定修订，核对修改文件及失效选用；涉及文件交付时核对实际内容。仅本地保存或上传完成不能报告项目已归档。

素材增删改查按[修订与恢复](references/revisions.md#5-创作中的素材增删改查)处理。已有绑定、上下文及服务可查的信息自行取得，不让用户提供技术 ID 或执行同步命令。创作和查询前读取最新项目内容，按需下载，保护本地未提交修改。角色页使用的领域内容与文件必须一致；助手经 MCP 回读角色页消费的领域文档与文件引用完成核验，不打开浏览器，也不把技术验收交给用户。

Blender 的实例 `project.save` 已完成服务内上传与项目提交，取得结果后回读即可，不在客户端重复提交同一检查点。

## 3. 中断与冲突

超时先查询原 `project.commit_status`，工作副本使用 `resume`。保留原 commit_id、上传状态和 journal，不新建任务或改幂等键掩盖未知结果。

修订冲突保留草稿与原请求，读取新 head 并明确合并，再用新 commit_id 提交；工作副本项目规则声明了权威源时按其合并。禁止将旧全文直接套到最新基线强制覆盖。
本地有改动或待恢复操作时不切换基线。

跨电脑只恢复已提交内容。原生生成任务按 service_id/run_id 查询，文件下载成功不代表 Blender 已加载工程。

## 4. 交付

用户明确要求删除项目时，先发现 `project.delete`、`project.restore`、`project.lifecycle_status` 的在线 Schema。仅所有者可操作；用 list/open 返回的最新 head 和 lifecycle_version 建立请求，发送前持久保存原参数和稳定 change_id。删除后内容不能打开或编辑，修订和共享素材保留；恢复通过 `project.list(deleted=true)` 定位，沿用同一项目 ID 和 head。

响应未知先查原 `project.lifecycle_status`，NOT_FOUND 时只重试原请求；PROJECT_BUSY 等原操作结束后重试。修订或生命周期冲突须刷新并重新确认目标，不能自动覆盖。删除授权仅适用于用户指定项目，不从本地文件缺失推导项目删除；不物理删除 Artifact 或 World 内容。

任务记录保存项目与固定修订、修改摘要、失效选用和验证证据；正常回复只需交付创作成果、同步结果与查看位置。未完成时准确说明本地与线上状态、保留位置及恢复入口，不要求用户补同步提示词。MCP 回读核验未完成不得声称已同步。
固定资产引用按实际请求使用 `project.use_asset`；不自动发布到 World、升级资产版本或修改成员。不因本地缺失或推断删除远端内容；用户明确指定的素材候选按[修订与恢复](references/revisions.md#5-创作中的素材增删改查)直接删除，不再确认是否同步。
