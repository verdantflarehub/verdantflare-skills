---
name: verdantflare-project
description: Create, open, save and recover VerdantFlare Studio projects and local working copies through Studio MCP. Use for project revisions, explicit file imports and fixed asset references; not for generic software planning or Git repositories.
---

# VerdantFlare Project

## 1. 连接与范围

通过宿主 Studio `/mcp` 管理工程。沿用 `STUDIO_MCP_URL` 和 `STUDIO_MCP_BEARER_TOKEN`；Bearer 代表真实用户，不增加登录、密码或用户/组织请求头。
先发现当前工具，确认本次任务需要的能力及参数。工具可见不代表有项目写权限。

优先使用宿主 MCP。需要本地文件读写时使用 `studio-workspace`，操作见[工作副本](references/workspace.md)。
直接提交、固定资产引用或恢复未知结果时，读取[修订与恢复](references/revisions.md)。两种方式使用同一用户凭据。

## 2. 打开与保存

1. 沿用用户明确的项目，或通过 `project.list` 定位。存在无法区分的多个候选才询问。新建用 `project.create`，项目 ID 取自返回值，不用目录别名或 `default`。
2. 用 `project.open` 固定基线修订，区分所选 `revision_id` 与当前 `head_revision_id`。按需取文件，保护本地修改；没有下载的文件不是删除指令。
3. 明确本次修改的文本、文件、选用与任务引用。小文本可直接提交；二进制先经 Artifact 获得 ContentRef。不要扫描整目录替用户选择成果。
4. 发送前持久保存原请求及稳定 `commit_id`。用 `expected_revision_id` 提交，正文和相关选用在同一修订更新。请求文件不含凭据。
5. 回读返回的固定修订，核对修改文件及失效选用；涉及文件交付时核对实际内容。仅本地保存或上传完成不能报告项目已归档。

Blender 的实例 `project.save` 已完成服务内上传与项目提交，取得结果后回读即可，不在客户端重复提交同一检查点。

## 3. 中断与冲突

超时先查询原 `project.commit_status`，工作副本使用 `resume`。保留原 commit_id、上传状态和 journal，不新建任务或改幂等键掩盖未知结果。

修订冲突保留草稿与原请求，读取新 head 并明确合并，再用新 commit_id 提交。禁止将旧全文直接套到最新基线强制覆盖。
本地有改动或待恢复操作时不切换基线。

跨电脑只恢复已提交内容。原生生成任务按 service_id/run_id 查询，文件下载成功不代表 Blender 已加载工程。

## 4. 交付

返回项目与固定修订、修改摘要、失效选用和验证结果。未完成时说明保留位置及恢复入口。
固定资产引用按实际请求使用 `project.use_asset`；不自动发布到 World、升级资产版本、修改成员或删除远端内容。
