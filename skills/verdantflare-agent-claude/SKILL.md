---
name: verdantflare-agent-claude
description: 通过本机 MCP 服务向指定 VS Code Claude Code（克劳德）面板会话或受管 CLI 派单、查询进度、续接任务、取消轮次及读取结果。用于其他智能体委托克劳德执行问题诊断、方案设计、评审与工作区任务，不代替最终人工审批。
---

# Claude Code 智能体调度（verdantflare-agent-claude）

## 1. 编码规范

文档编码 `CC`；章节编码为两位；图表编号采用 `[CC][章节两位][字母与数字两位]`。技能可全局安装，运行服务通过本机配置定位。

## 2. 范围与边界

其他智能体通过八项 MCP 工具命名会话、委托 Claude Code（克劳德），跟踪持久任务并回收结果。支持向指定的 VS Code Claude Code 面板在线会话注入任务（`panel`），也支持显式选择受管 CLI 流式会话（`managed`）。运行时自动发现最新已安装扩展与内置 CLI，不锁定扩展版本号。

本技能不代替人工决策或生产发布授权。调用者先确认工作区与目标会话状态，不能把离线历史 `.jsonl` 可读等同于 VS Code 面板会话在线可控。

Agent 间委托统一先应用 `verdantflare-meta-agent`：绑定计划、稳定任务身份和关联变更，登记 Session 名称、原生地址、执行及回传目标，再按本技能能力派单。通用协作规则不替代下述宿主权限、互斥、防递归和轮次校验；投递受理不等于接收方处理或任务验收。

## 3. 能力清单与按任务读取

按要执行的原子能力直达对应入口：

| 原子能力（能干什么活） | 入口文档                                                          |
| :--------------------- | :---------------------------------------------------------------- |
| 派单续接               | [01.dispatch.entrypoint.md](references/01.dispatch.entrypoint.md) |
| 安装启动               | [02.install.entrypoint.md](references/02.install.entrypoint.md)   |

先读当前任务对应能力入口，无需通读其他能力文档。

## 4. 工具与命令

优先使用宿主已连接的 `verdantflare-agent-claude` MCP 工具。服务端包自包含于 `scripts/verdantflare_agent_claude/`，[install.py](scripts/install.py) 负责同步技能至 `~/.agents/skills/`、构建独立虚拟环境运行时并向对端智能体注册 MCP，[launch.py](scripts/launch.py) 按持久安装配置（或 `VERDANTFLARE_AGENT_CLAUDE_SERVICE`）启动服务与 CLI：

```powershell
python -X utf8 scripts/install.py --replace --register-mcp
python -X utf8 scripts/launch.py --call claude_capabilities
python -X utf8 scripts/launch.py
```
