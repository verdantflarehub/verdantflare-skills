---
name: verdantflare-meta-agent
description: 规范 AI Agent 之间基于计划的委托、Session 命名与身份登记、结果回传、审阅、统筹和故障恢复。用于跨 Agent 派单、交接、回收结果或维护协作机制；普通问答不强制建计划，不替代用户授权、项目验收和各宿主调度技能。
---

# Agent 计划协作治理

## 1. 范围与边界

统一 Agent 之间的任务来源、Session 身份、交接与通知闭环，确保执行方知道做什么、向谁回传，统筹方知道何时审阅、返修、解锁依赖及结项。可独立安装，适用于不同仓库；负责人、计划结构和实际授权遵循目标仓库规则。

与 `verdantflare-meta-markdown` 协作维护文档表达，与 `verdantflare-agent-codex`、`verdantflare-agent-claude`、`verdantflare-agent-antigravity`、`verdantflare-agent-grok` 协作执行宿主通信。本技能不授予发布、提权或向无关 Session 派单的权限。

## 2. 按任务读取

| 当前任务               | 读取资料                                     |
| :--------------------- | :------------------------------------------- |
| 判断是否适用、选择分支 | [分类与边界](references/01.base-category.md) |
| 执行操作与失败恢复     | [执行流程](references/01.base-workflow.md)   |
| 核对约束、默认值与例外 | [规则约束](references/01.base-rules.md)      |
| 验证、门禁与交付报告   | [验收标准](references/01.base-acceptance.md) |

先读当前任务对应专册，不要求通读全部专册。

## 3. 工具与命令

[交接检查器](scripts/check_handoff.py) 只读验证交接字段和工作区路径；[交接样例](resources/handoff.example.json) 展示输入格式。Python 3.10 及以上，仅用标准库。

```text
python scripts/check_handoff.py --workspace . --input handoff.json
python -m unittest discover -s scripts -p test_*.py
```
