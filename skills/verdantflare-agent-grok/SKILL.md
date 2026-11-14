---
name: verdantflare-agent-grok
description: 通过本机 MCP 服务向 Grok（古若科）派单、查询进度、续接任务、取消轮次及读取结果，并同步写入会话索引以在 VS Code AFK Pilot 面板展示。用于其他智能体委托古若科执行图片/界面素材高清化重置与工作区任务，不代替人工审美终审。
---

# Grok 与 AFK Pilot 智能体调度（verdantflare-agent-grok）

## 1. 编码规范

文档编码 `GK`；章节编码为两位；图表编号采用 `[GK][章节两位][字母与数字两位]`。技能可全局安装，运行服务通过本机配置定位。

## 2. 范围与边界

其他智能体通过七项 MCP 工具委托 Grok（古若科），跟踪持久任务并回收生成的媒体与代码产物。由于 Grok 无官方第一方 VS Code 扩展，默认采用 `acp` 模式（`grok agent --no-leader stdio`）落盘标准会话并自动同步 `client-state/session-meta.json`，使任务在 VS Code **AFK Pilot**（`grok.chat` 与 `grok.projects` 视图）中直接展示与回放；运行时自动探测最新有效 AFK Pilot 扩展与 Grok CLI，不锁定扩展版本号。

本技能不代替最终审美拍板或生产发布授权。首轮视觉母版仍须呈交人工审定后方可批量衍生。

Agent 间委托统一先应用 `verdantflare-meta-agent`：绑定计划、稳定任务身份和关联变更，登记 Session 名称、原生地址、执行及回传目标，再按本技能能力派单。通用协作规则不替代下述宿主权限、互斥、防递归和轮次校验；投递受理不等于接收方处理或任务验收。

## 3. 按任务读取

| 当前任务                  | 读取资料                                     |
| :------------------------ | :------------------------------------------- |
| 判断模式与 AFK Pilot 能力 | [分类与边界](references/01.base-category.md) |
| 派单、续接与面板同步      | [执行流程](references/01.base-workflow.md)   |
| 权限、并发及幂等约束      | [规则约束](references/01.base-rules.md)      |
| 测试与面板展示判定        | [验收标准](references/01.base-acceptance.md) |

## 4. 工具与命令

优先使用宿主已连接的 `verdantflare-agent-grok` MCP 工具。服务端包自包含于 `scripts/verdantflare_agent_grok/`，[install.py](scripts/install.py) 负责同步技能至 `~/.agents/skills/`、构建独立虚拟环境运行时并向对端智能体注册 MCP，[launch.py](scripts/launch.py) 按持久安装配置（或 `VERDANTFLARE_AGENT_GROK_SERVICE`）启动服务与 CLI：

```powershell
python -X utf8 scripts/install.py --replace --register-mcp
python -X utf8 scripts/launch.py --call grok_capabilities
python -X utf8 scripts/launch.py
```
