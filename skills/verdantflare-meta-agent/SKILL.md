---
name: verdantflare-meta-agent
description: 规范 AI Agent 之间基于计划的委托、Session 命名与身份登记、多智能体评审分工（Claude 架构核验与抽查低 Token 门禁、Codex 细节与验收全面核验）、结果回传、统筹和故障恢复。用于跨 Agent 派单、交接、回收结果或维护协作机制；普通问答不强制建计划，不替代用户授权、项目验收和各宿主调度技能。
---

# Agent 计划协作治理（verdantflare-meta-agent）

## 1. 编码规范

文档编码 `MA`；章节编码使用两位数字；需要图表时采用 `[MA][章节两位][字母与数字两位]`，例如 `MA02A1`。本技能为 `verdantflare-meta-*` 跨仓库基础治理技能，仅使用 Python 标准库，不依赖特定仓库业务资产，支持独立全局安装。

## 2. 范围与边界

统一 Agent 之间的任务来源、Session 身份、评审主责边界、交接门禁与通知闭环，确保执行方明确范围与回传目标，统筹方明确审阅、返修、解锁依赖及结项规则。在多智能体研发评审中执行严格专精分工：Claude 仅承担 `T1` 总体架构核验与验收流程修订定稿、`T4` 抽查 Codex 评价 `1～2` 项与跨系统架构争议裁决（`HO-CLAUDE-SCOPE` 低 Token 门禁，不审源码与脚本细节），Codex 承担全部具体工程细节评审、影响防漏扫描与对照锁卷流程的验收结果全面核验。

与 `verdantflare-write-plan`、`verdantflare-write-markdown` 协作维护计划门禁和文档表达，与 `verdantflare-agent-codex`、`verdantflare-agent-claude`、`verdantflare-agent-antigravity`、`verdantflare-agent-grok` 协作执行宿主通信。本技能不授予发布、提权或向无关 Session 派单的权限。

## 3. 能力清单与按任务读取

按当前要执行的原子能力直达对应入口；跨能力通用约束见 `00.common.rules.md`：

| 原子能力（能干什么活） | 入口文档                                                                        |
| :--------------------- | :------------------------------------------------------------------------------ |
| 派单委托               | [01.delegate.entrypoint.md](references/01.delegate.entrypoint.md)               |
| 评审分工               | [02.review-division.entrypoint.md](references/02.review-division.entrypoint.md) |
| 运行恢复               | [03.runtime.entrypoint.md](references/03.runtime.entrypoint.md)                 |

先读目标能力的 `entrypoint.md`，无需通读其他能力文档；跨能力公共约束（计划绑定、Session 命名、权限与反自审红线）见 [00.common.rules.md](references/00.common.rules.md)。

## 4. 工具与命令

[交接检查器](scripts/check_handoff.py) 只读验证 20 个必填交接字段、成对可选评审字段（`review_stage`、`review_doc_path`）、工作区相对路径与 `HO-CLAUDE-SCOPE` 低 Token 架构门禁；[交接样例](resources/handoff.example.json) 展示标准 20 字段非 Claude 交接格式。[安装脚本](scripts/install.py) 将本技能同步至 `~/.agents/skills/verdantflare-meta-agent` 并逐文件校验哈希。

[协作运行时](scripts/agent_runtime.py) 提供工作区绑定账本和持久 worker；宿主适配器须在配置中明确选择，缺少适配器时返回错误，不自动换宿主。

```text
python -X utf8 scripts/check_handoff.py --workspace . --input handoff.json
python -X utf8 scripts/install.py
python -X utf8 -m unittest discover -s scripts -p "test_*.py"
```
