# VF 全局技能安装清单

## 1. 安装范围

在 VF 开发机上，将下列跨项目技能安装到 `~/.agents/skills/`。按职责选装的技能列在第 2 节。此清单用于新机配置和更新已有机器。

| 全局技能 | 跨项目用途 |
| --- | --- |
| [verdantflare-download](verdantflare-download/SKILL.md) | 通用下载与资料研究 |
| [verdantflare-meta-markdown](verdantflare-meta-markdown/SKILL.md) | 文档编写、格式与检查 |
| [verdantflare-meta-plan](verdantflare-meta-plan/SKILL.md) | 研发计划与验收 |
| [verdantflare-meta-skill](verdantflare-meta-skill/SKILL.md) | 技能设计与安装 |
| [verdantflare-meta-googledrive](verdantflare-meta-googledrive/SKILL.md) | 跨机素材管理 |
| [verdantflare-meta-agent](verdantflare-meta-agent/SKILL.md) | 多助手协作 |

## 2. 按职责选装

配有对应宿主、需要派单的工作机，全局安装对应调度技能。编制白皮书的工作机可全局安装白皮书技能。

- [verdantflare-agent-antigravity](verdantflare-agent-antigravity/SKILL.md) 用于Antigravity 调度。
- [verdantflare-agent-claude](verdantflare-agent-claude/SKILL.md) 用于Claude 调度。
- [verdantflare-agent-codex](verdantflare-agent-codex/SKILL.md) 用于Codex 调度。
- [verdantflare-agent-grok](verdantflare-agent-grok/SKILL.md) 用于Grok 调度。
- [verdantflare-technical-whitepaper](verdantflare-technical-whitepaper/SKILL.md) 用于技术白皮书与架构图。

本目录其余 Studio、媒体与项目业务技能按项目或产品交付要求安装。

## 3. 安装与验证

从本目录复制完整技能包到统一安装根的同名目录。更新前核对已有副本的本地差异。安装后核对文件并从新会话验证自然语言触发。各宿主的发现方式见[安装与接入](verdantflare-meta-skill/references/install.md)。
