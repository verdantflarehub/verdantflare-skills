---
name: verdantflare-agent-claude
description: 通过本机 MCP 服务向指定 VS Code Claude Code（克劳德）面板会话或受管 CLI 派单、查询进度、续接任务、取消轮次及读取结果。用于其他智能体委托克劳德执行问题诊断、方案设计、评审与工作区任务，不代替最终人工审批。
---

# Claude Code 智能体调度（verdantflare-agent-claude）

## 1. 编码规范

文档编码 `CC`；章节编码为两位；图表编号采用 `[CC][章节两位][字母与数字两位]`。技能可全局安装，运行服务通过本机配置定位。

## 2. 范围与边界

其他智能体通过七项 MCP 工具委托 Claude Code（克劳德），跟踪持久任务并回收结果。支持向指定的 VS Code Claude Code 面板在线会话注入任务（`panel`），也支持显式选择受管 CLI 流式会话（`managed`）。运行时自动发现最新已安装扩展与内置 CLI，不锁定扩展版本号。

本技能不代替人工决策或生产发布授权。调用者先确认工作区与目标会话状态，不能把离线历史 `.jsonl` 可读等同于 VS Code 面板会话在线可控。

## 3. 按任务读取

| 当前任务             | 读取资料                                     |
| :------------------- | :------------------------------------------- |
| 判断模式与会话能力   | [分类与边界](references/01.base-category.md) |
| 派单、续接与故障恢复 | [执行流程](references/01.base-workflow.md)   |
| 权限、并发及幂等约束 | [规则约束](references/01.base-rules.md)      |
| 测试与交付判定       | [验收标准](references/01.base-acceptance.md) |

## 4. 工具与命令

优先使用宿主已连接的 `verdantflare-agent-claude` MCP 工具。服务端包自包含于 `scripts/verdantflare_agent_claude/`，[install.py](scripts/install.py) 负责同步技能至 `~/.agents/skills/`、构建独立虚拟环境运行时并向对端智能体注册 MCP，[launch.py](scripts/launch.py) 按持久安装配置（或 `VERDANTFLARE_AGENT_CLAUDE_SERVICE`）启动服务与 CLI：

```powershell
python -X utf8 scripts/install.py --replace --register-mcp
python -X utf8 scripts/launch.py --call claude_capabilities
python -X utf8 scripts/launch.py
```
