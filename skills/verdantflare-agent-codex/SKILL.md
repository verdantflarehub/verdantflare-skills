---
name: verdantflare-agent-codex
description: 通过本机 MCP 服务向指定 Codex 插件会话派单、查询进度、续接任务、取消轮次及读取结果；支持可选的 Windows VS Code 当前工作区会话列表补丁。用于跨智能体调度和本地插件维护，不代替上游方案设计或人工审批。
---

# Codex 智能体调度（verdantflare-agent-codex）

## 1. 编码规范

文档编码 `AC`；章节编码为两位；图表编号采用 `[AC][章节两位][字母与数字两位]`。技能可全局安装，运行服务通过本机配置定位。

## 2. 范围与边界

其他智能体通过八项 MCP 工具命名会话、委托 Codex，跟踪持久任务并验收产物。不同插件会话按 `thread_id` 并行，同一会话仍互斥；恢复仅核对原提交，不重发未知任务。派单目标为用户指定的插件会话。运行时自动探测当前已安装的 Codex 扩展目录与 IPC 方法版本，不锁定扩展版本号。

本技能不代替方案决策、权限审批或最终交付验收。调用者先确认任务授权与工作区，不能把历史可读当成插件正在执行的会话可控。另提供实验性的当前工作区会话列表补丁，显式启用、独立兼容检测和回滚；安装文件核验与真实 UI 验收分别记录。

Agent 间委托统一先应用 `verdantflare-meta-agent`：绑定计划、稳定任务身份和关联变更，登记 Session 名称、原生地址、执行及回传目标，再按本技能能力派单。通用协作规则不替代下述宿主权限、互斥、防递归和轮次校验；投递受理不等于接收方处理或任务验收。

## 3. 能力清单与按任务读取

按要执行的原子能力直达对应入口；跨能力共享约束见 `00.common.rules.md`：

| 原子能力（能干什么活） | 入口文档                                                                              |
| :--------------------- | :------------------------------------------------------------------------------------ |
| 派单续接               | [01.dispatch.entrypoint.md](references/01.dispatch.entrypoint.md)                     |
| 安装启动               | [02.install.entrypoint.md](references/02.install.entrypoint.md)                       |
| 会话补丁               | [03.workspace-sessions.entrypoint.md](references/03.workspace-sessions.entrypoint.md) |

先读当前任务对应能力入口，无需通读其他能力文档。

跨能力公共文档：[00.common.rules.md](references/00.common.rules.md)（由各能力入口引用）。

## 4. 工具与命令

优先使用宿主已连接的 `verdantflare-agent-codex` MCP 工具。服务端包自包含于 `scripts/verdantflare_agent_codex/`，[install.py](scripts/install.py) 负责同步技能至 `~/.agents/skills/`、构建独立虚拟环境运行时并向对端智能体注册 MCP，[launch.py](scripts/launch.py) 按持久安装配置（或 `VERDANTFLARE_AGENT_CODEX_SERVICE`）启动服务与 CLI：

```powershell
python -X utf8 scripts/install.py --replace --register-mcp
python -X utf8 scripts/launch.py --call codex_capabilities
python -X utf8 scripts/launch.py
```

可选显示补丁由 [workspace_sessions.py](scripts/workspace_sessions.py) 管理，先运行 `python scripts/workspace_sessions.py probe`，完整操作和验证边界见 [03.workspace-sessions.entrypoint.md](references/03.workspace-sessions.entrypoint.md)。
