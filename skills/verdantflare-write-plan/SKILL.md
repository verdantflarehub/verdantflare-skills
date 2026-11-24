---
name: verdantflare-write-plan
description: 对研发计划执行双轨分级管控（简单计划 S0/S1 与复杂研发计划 T1–T5）、核心受控目录（plan/changes/reviews/acceptance）对账、锁卷与评审确定性哈希计算及五档 AST 自动门禁检查。用于研发立项定级、编制或升级计划、审定验收流程、准备 CP0 图文方案报审、组织 T4 取证核验与抽查、呈报 CP1 人类验收及同构归档检查；不替代具体业务方案设计与人工终审决策。
---

# 研发计划分级管控与核心目录治理（verdantflare-write-plan）

## 1. 编码规范

文档编码 `MP`；章节编码使用两位数字；需要图表时采用 `[MP][章节两位][字母与数字两位]`，例如 `MP02A1`。本技能属于 `verdantflare-meta-*` 跨仓库基础治理技能，内置自包含专册、模板与检查脚本，不硬编码单一仓库状态，可独立安装至全局技能目录。

## 2. 范围与边界

本技能管理研发计划的分级准入、阶段结构、多智能体审核主责边界、四大受控目录（`docs/plan/`、`docs/changes/`、`docs/reviews/`、`docs/acceptance/`）生命周期及 `CP0 / CP1 / archive` 自动门禁：

- **双轨分流**：按影响域将计划分为 `简单计划`（2～3 步，支持 `S0` 零人工参与或 `S1` 单点确认，免独立评审验收目录）与 `复杂研发计划`（固定 `T1–T5` 五阶段与 `CP0 / CP1` 双门禁）。
- **验收三方分工**：执行者起草验收流程并亲自取证；Codex（或 `D4-A` 非作者研发替补）修订流程、包办工程细节与全仓防漏，并全面核验验收结果；Claude 做架构核验、修订定稿验收流程，并只抽查 Codex 评价 `1～2` 项。不以怀疑造假为前提，不重跑、不全面复核。
- **人类单一决定**：`CP0` 与 `CP1` 各只有一个动作——批准，或不批准并给意见；`T5` 只设 `T5.1` 一项，批准后助手直接收尾。
- **协作边界**：Markdown 排版与 Prettier 格式化交 `verdantflare-write-markdown`；跨智能体交接单低 Token 校验交 `verdantflare-meta-agent`；图片受管入库交 `verdantflare-meta-googledrive`。目标仓库 `AGENTS.md` 有细化规则时叠加生效。

## 3. 能力清单与按任务读取

按当前要执行的原子能力直达对应入口；跨能力红线与退出码字典见 `00.common.*`：

| 原子能力（能干什么活） | 入口文档                                                                  |
| :--------------------- | :------------------------------------------------------------------------ |
| 简单计划               | [01.simple-plan.entrypoint.md](references/01.simple-plan.entrypoint.md)   |
| 复杂计划               | [02.complex-plan.entrypoint.md](references/02.complex-plan.entrypoint.md) |
| 门禁哈希               | [03.gate-hash.entrypoint.md](references/03.gate-hash.entrypoint.md)       |

跨能力红线（双棘轮防降级、四受控目录与反自审）见 [00.common.rules.md](references/00.common.rules.md)，三值退出码与 `PL-*` 规则码字典见 [00.common.acceptance.md](references/00.common.acceptance.md)；脚手架模板位于 `resources/templates/`，原生收据、目标来源与委托政策专册由 [03.gate-hash.entrypoint.md](references/03.gate-hash.entrypoint.md) 统一索引。

## 4. 工具与命令

主检查器 [plan_lint.py](scripts/plan_lint.py)、原生收据校验器 [native_receipts.py](scripts/native_receipts.py)、全局安装器 [install.py](scripts/install.py) 与单测套件 [test_plan_lint.py](tests/test_plan_lint.py)、[test_native_receipts.py](tests/test_native_receipts.py) 均使用 UTF-8 运行：

```text
python -X utf8 scripts/plan_lint.py classify --text "<需求描述>" --json
python -X utf8 scripts/plan_lint.py init --level complex --slug <topic> --title "<标题>" --owner <name> --with-core-docs
python -X utf8 scripts/plan_lint.py lock-hash docs/acceptance/accept_YYYYMMDD_<topic>.md --json
python -X utf8 scripts/plan_lint.py review-hash --stage t2 --design docs/design/spec.md --accept docs/acceptance/accept.md
python -X utf8 scripts/plan_lint.py check docs/plan/YYYY.MM.<topic>.plan.md --gate cp0
python -X utf8 scripts/plan_lint.py freeze --root . --repo-id <org/repo>
python -X utf8 scripts/install.py --replace
python -X utf8 -m unittest discover -s tests -p "test_*.py"
```
