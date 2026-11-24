---
name: verdantflare-blender
description: 通过指定 VerdantFlare Blender 实例的 Studio MCP 连接检视与编辑实时场景、执行数字片场批处理、将工程与三维片场包归档至 Project 并恢复已知操作。用于实时 3D 场景编辑与工程归档；不用于 Blender 教程、离线代码评审或应用部署。
---

# VerdantFlare Blender

## 1. 范围与边界

本技能通过用户指定的 Studio Blender 实例连接（如 `/mcp/blenderA`）检视和编辑实时场景、运行受控数字片场批处理脚本、执行工程与 Set Package 归档及中断恢复；不用于 Blender 教程、离线代码评审或应用部署。

AI 短剧任务形成阶段成果后默认同步到原 Studio 项目并沿用真实 `project_id`，用户指定仅本地或只读讨论时遵循其范围。候选、返修与最终结果保留固定版本、修改原因、来源设定和任务记录，按当前 Project/Artifact 契约提交并回读修订（只上传或本地保存不算已同步）；响应未知先对账恢复，不重复收费生成；同步不自动批准、选用或发布 World。本技能为仓库内技能，按需与 `verdantflare-project`、`verdantflare-artifact` 协同。

## 2. 能力清单与按任务读取

按要执行的原子能力直达对应入口：

| 原子能力（能干什么活） | 入口文档                                                                  |
| :--------------------- | :------------------------------------------------------------------------ |
| 实时编辑               | [01.live-edit.entrypoint.md](references/01.live-edit.entrypoint.md)       |
| 归档导出               | [02.project-save.entrypoint.md](references/02.project-save.entrypoint.md) |

## 3. 工具与命令

实例入口使用指定实例 `/mcp/<instance>`，与中央 `/mcp` 分别发现工具，复用同一有效 Studio Bearer（`STUDIO_MCP_URL`、`STUDIO_MCP_BEARER_TOKEN`）。常用实例工具与片场包校验命令如下（完整参数与恢复流程见对应能力入口）：

```text
实例 MCP 工具：session.open / session.close / scene.get / object.create / object.update_transform
实例扩展工具：workspace.clean / script.run_project / scene.inspect_file / scene.open_file / operation.get
工程归档工具：scene.save / project.save / project.export_assets
python scripts/set-package/validate_set_package.py <package_dir>
python scripts/set-package/check_camera_alignment.py --package-dir <package_dir> --output <alignment.json>
```
