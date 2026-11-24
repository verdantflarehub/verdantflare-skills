---
name: verdantflare-write-markdown
description: 编写、整理和审阅任意仓库中自主维护的 Markdown，按通用规范及 README、设计、计划、变更、研发评审、研发验收、缺陷、技能文档业务规范组织内容，执行落盘后 Format、结构检查与交付流程，避免多层列表缩进。用于新增或实质修改 Markdown、长列表重构及 Format 缩进排查；目标仓库规则、第三方原文、历史归档和脚本依赖的格式契约优先。
---

# Markdown 结构与格式化治理（verdantflare-write-markdown）

## 1. 编码规范

文档编码 `MM`；章节编码使用两位数字；需要图表时采用 `[MM][章节两位][字母与数字两位]`，例如 `MM03A1`。本技能为 `verdantflare-meta-*` 基础技能，不依赖任何仓库的业务资产，可独立全局安装。

## 2. 范围与边界

本技能把用户“不喜欢自动 Format 后层层缩进”的偏好落实到 Markdown 源码结构，适用于所有仓库中自主维护的 Markdown：负责标题分层与浅层列表重构、按文档主类型组织内容、自动探测 Prettier 执行 Format，以及量化结构检查与全局安装。新增或实质修改时应用；仅改链接、纠正错字时不顺带重排全文，也不把文档整理扩展为业务设计重做或全仓重排。

目标仓库规则（含强制模板、生成脚本同步区域和媒体排版契约）与用户全局规则优先于本技能默认规范；第三方原文、历史归档（`docs/archive/`）、代码示例内容和机器生成区域保留既有格式。技能文档的统一入口结构与体积上限与 `verdantflare-meta-skill` 协作，以 `verdantflare-meta-skill` 为准。

## 3. 能力清单与按任务读取

按当前要执行的原子能力直达对应入口；跨能力通用排版规则见 `00.common.rules.md`：

| 原子能力（能干什么活） | 入口文档                                                                  |
| :--------------------- | :------------------------------------------------------------------------ |
| 格式检查               | [01.format-check.entrypoint.md](references/01.format-check.entrypoint.md) |
| 文档编写               | [02.author-doc.entrypoint.md](references/02.author-doc.entrypoint.md)     |
| 全局安装               | [03.install.entrypoint.md](references/03.install.entrypoint.md)           |

先读目标能力的 `entrypoint.md`（按文档主类型编写时再只加载对应的一份主题专册）；跨能力规则优先级、标题分层、表格与设置边界见 [00.common.rules.md](references/00.common.rules.md)。

## 4. 工具与命令

脚本位于本技能安装目录的 `scripts/` 下；从其他目录调用时使用已发现的实际路径，不假定目标仓库包含本技能。依赖见 [requirements.txt](scripts/requirements.txt)，主脚本与测试为 [md_format.py](scripts/md_format.py)、[check_md.py](scripts/check_md.py)、[install.py](scripts/install.py) 与 [test_verdantflare_markdown.py](scripts/test_verdantflare_markdown.py)：

```text
python -X utf8 scripts/md_format.py --check docs/example.md
python -X utf8 scripts/md_format.py docs/example.md
python -X utf8 scripts/check_md.py docs/example.md
python -X utf8 scripts/install.py
python -X utf8 -m unittest discover -s scripts -p "test_*.py"
```
