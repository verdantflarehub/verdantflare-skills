---
name: verdantflare-agent-codex
description: 通过本机 MCP 服务向指定 Codex 插件会话派单、查询进度、续接任务、取消轮次及读取结果；也支持显式选择独立 Codex 后端。用于其他智能体委托 Codex 执行工作区任务及维护调度接入，不代替上游方案设计或人工审批。
---

# Codex 智能体调度（verdantflare-agent-codex）

## 1. 编码规范

文档编码 `AC`；章节编码为两位；图表编号采用 `[AC][章节两位][字母与数字两位]`。技能可全局安装，运行服务通过本机配置定位。

## 2. 范围与边界

其他智能体通过七项 MCP 工具委托 Codex，跟踪持久任务并验收产物。默认业务目标是用户指定的插件会话；独立执行模式由调用者明确选择。运行时自动探测当前已安装的 Codex 扩展目录与 IPC 方法版本，不锁定扩展版本号。

本技能不代替方案决策、权限审批或最终交付验收。调用者先确认任务授权与工作区，不能把历史可读当成插件正在执行的会话可控。

Agent 间委托统一先应用 `verdantflare-meta-agent`：绑定计划、稳定任务身份和关联变更，登记 Session 名称、原生地址、执行及回传目标，再按本技能能力派单。通用协作规则不替代下述宿主权限、互斥、防递归和轮次校验；投递受理不等于接收方处理或任务验收。

## 3. 按任务读取

| 当前任务             | 读取资料                                     |
| :------------------- | :------------------------------------------- |
| 判断模式与会话能力   | [分类与边界](references/01.base-category.md) |
| 派单、续接与故障恢复 | [执行流程](references/01.base-workflow.md)   |
| 权限、并发及幂等约束 | [规则约束](references/01.base-rules.md)      |
| 测试与交付判定       | [验收标准](references/01.base-acceptance.md) |

## 4. 工具与命令

优先使用宿主已连接的 `verdantflare-agent-codex` MCP 工具。服务端包自包含于 `scripts/verdantflare_agent_codex/`，[install.py](scripts/install.py) 负责同步技能至 `~/.agents/skills/`、构建独立虚拟环境运行时并向对端智能体注册 MCP，[launch.py](scripts/launch.py) 按持久安装配置（或 `VERDANTFLARE_AGENT_CODEX_SERVICE`）启动服务与 CLI：

```powershell
python -X utf8 scripts/install.py --replace --register-mcp
python -X utf8 scripts/launch.py --call codex_capabilities
python -X utf8 scripts/launch.py
```
