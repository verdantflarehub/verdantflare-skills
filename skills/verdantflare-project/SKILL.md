---
name: verdantflare-project
description: 通过 VerdantFlare Studio MCP 创建、打开、保存、恢复项目并维护素材展示关联。用于已绑定 Studio 创作项目的素材增删改查、修订同步、生命周期管理与页面展示关联校验；不用于通用软件规划或 Git 仓库管理。
metadata:
  version: "0.1.1"
---

# VerdantFlare Project

## 1. 范围与边界

本技能通过宿主 Studio `/mcp` 及 `studio-workspace` 管理 VerdantFlare Studio 项目全生命周期，覆盖项目创建、打开、修订提交、中断冲突恢复、素材增删改查、固定资产引用、项目删除恢复以及页面展示关联核验；不用于通用软件规划或 Git 仓库管理。

已绑定 Studio 项目的创作任务默认完成素材增删改查所需的传输、项目提交与角色关联核验并沿用真实 `project_id`，用户无需另说上传、下载、同步或检查网站；仅本地、只讨论或暂不上传的明确限定优先。只上传或本地保存不算已同步，响应未知先对账恢复，不重复收费生成；同步不自动批准、选用或发布 World；技能负责助手任务期间的收尾，不承诺无人运行时的文件监控。本技能为仓库内技能，按需与 `verdantflare-artifact`、`verdantflare-blender` 协同。

## 2. 能力清单与按任务读取

按要执行的原子能力直达对应入口；MCP 授权边界与本地工作副本规范见公共专册：

| 原子能力（能干什么活） | 入口文档                                                                        |
| :--------------------- | :------------------------------------------------------------------------------ |
| 项目读写               | [01.project-ops.entrypoint.md](references/01.project-ops.entrypoint.md)         |
| 展示关联               | [02.display-binding.entrypoint.md](references/02.display-binding.entrypoint.md) |

跨能力公共文档：[00.common.rules.md](references/00.common.rules.md)（由各能力入口引用）。

## 3. 工具与命令

优先使用宿主中央 Studio `/mcp`；需要本地工作副本或无宿主工具时使用 Studio 0.5.50 的 `studio-workspace`；展示关联静态只读检查使用 [check_video_display.py](scripts/check_video_display.py)（单元测试位于 [test_check_video_display.py](scripts/test_check_video_display.py)）。常用命令如下（完整参数见对应能力入口）：

```text
studio-workspace tools
studio-workspace call --input request.json
studio-workspace status --dir <dir> --alias <alias> --project-id <project-id>
python -X utf8 scripts/check_video_display.py --manifest open.json --content project-content.json --versions metadata.json
python -X utf8 -m unittest discover -s scripts -p "test_*.py"
```
