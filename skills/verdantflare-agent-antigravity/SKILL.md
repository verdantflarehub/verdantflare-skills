---
name: verdantflare-agent-antigravity
description: 将 Google Antigravity（双子座）封装为标准 MCP 服务与 CLI 调度接口，供其他智能体直接向 VS Code 前台 Antigravity 面板注入任务、续接对话、监控执行状态并读取产物与轨迹日志。用于跨智能体派单给双子座执行、查询双子座会话与产物、注册或维护 Antigravity MCP 服务；不替代上游方案设计或人工终审。
---

# Antigravity 智能体 MCP 桥接与跨智能体调度（verdantflare-agent-antigravity）

## 1. 编码规范

文档编码 `AA`；章节编码使用两位数字；需要图表时采用 `[AA][章节两位][字母与数字两位]`，例如 `AA02A1`。本技能为跨工作区全局技能，不依赖特定仓库业务资产，可独立全局安装。

## 2. 范围与边界

本技能将 VS Code 中的 Google Antigravity 扩展（底层由 `agy.exe --hub` 提供 `LanguageServerService` Connect-RPC 服务）与无头 `agy.exe` CLI 封装为统一的 `stdio` MCP 服务端及命令行工具。其他智能体可通过本技能默认向 **VS Code 前台 Antigravity 面板** 实时注入指令、新建或续接指定会话（`cascadeId`）、轮询执行状态并读取 `brain/<conversation-id>/` 下的产物与 `transcript.jsonl` 轨迹。本技能只负责跨智能体通信调度与 MCP 服务自身维护，不替代方案设计或人工审批；在 Antigravity 内部调用自身子智能体应直接使用 `invoke_subagent`，管理插件商店不适用本技能。

Agent 间委托统一先应用 `verdantflare-meta-agent`：绑定计划、稳定任务身份和关联变更，登记 Session 名称、原生地址、执行及回传目标，再按本技能能力派单。通用协作规则不替代下述宿主权限、互斥、防递归和轮次校验；投递受理不等于接收方处理或任务验收。

## 3. 能力清单与按任务读取

按要执行的原子能力直达对应入口；跨能力共享规则见 `00.common.rules.md`：

| 原子能力（能干什么活） | 入口文档                                                                |
| :--------------------- | :---------------------------------------------------------------------- |
| 派单续接               | [01.dispatch.entrypoint.md](references/01.dispatch.entrypoint.md)       |
| 服务运维               | [02.service-ops.entrypoint.md](references/02.service-ops.entrypoint.md) |

先读当前任务对应能力入口，无需通读其他能力文档。

跨能力公共文档：[00.common.rules.md](references/00.common.rules.md)（由各能力入口引用）。

## 4. 工具与命令

主脚本 [agy_mcp_server.py](scripts/agy_mcp_server.py) 同时提供 MCP `stdio` 服务与 CLI 子命令，[install.py](scripts/install.py) 负责安装到 `~/.agents/skills/` 并向本机其他智能体注册 MCP，回归测试见 [test_agy_mcp_server.py](scripts/test_agy_mcp_server.py) 与 [test_agy_jobs.py](scripts/test_agy_jobs.py)：

```powershell
python -X utf8 scripts/agy_mcp_server.py status
python -X utf8 scripts/agy_mcp_server.py list
python -X utf8 scripts/agy_mcp_server.py dispatch "请读取并执行变更交接书" --workspace .
python -X utf8 scripts/install.py --replace --register-mcp
python -X utf8 -m unittest discover -s scripts -p "test_*.py" -v
```
