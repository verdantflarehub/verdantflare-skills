---
name: verdantflare-meta-plan
description: 对研发计划执行双轨分级管控（简单计划 S0/S1 与复杂研发计划 T1–T5）、核心受控目录（plan/changes/reviews/acceptance）对账、锁卷与评审确定性哈希计算及五档 AST 自动门禁检查。用于研发立项定级、编制或升级计划、准备 CP0 图文方案报审、执行 T4 独立验真、呈报 CP1 双抽检及同构归档检查；不替代具体业务方案设计与人工终审决策。
---

# 研发计划分级管控与核心目录治理（verdantflare-meta-plan）

## 1. 编码规范

文档编码 `MP`；章节编码使用两位数字；需要图表时采用 `[MP][章节两位][字母与数字两位]`，例如 `MP02A1`。本技能属于 `verdantflare-meta-*` 跨仓库基础治理技能，内置自包含专册、模板与检查脚本，不硬编码单一仓库状态，可独立安装至全局技能目录。

## 2. 范围与边界

本技能管理研发计划的分级准入、阶段结构、多智能体审核主责边界、四大受控目录（`docs/plan/`、`docs/changes/`、`docs/reviews/`、`docs/acceptance/`）生命周期及 `CP0 / CP1 / archive` 自动门禁：

- **双轨分流**：按影响域将计划分为 `简单计划`（`C1.simple`，2～3 步，支持 `S0` 零人工参与或 `S1` 单点确认，免独立评审验收目录）与 `复杂研发计划`（`C2.complex`，固定 `T1–T5` 五阶段与 `CP0 / CP1` 双门禁）。
- **低 Token 审核边界**：复杂计划中 Claude 仅负责 `T1` 单次总体架构核验与 `T4` 单次轻量架构符合性审计（禁碰具体代码、正则、参数、字节数或多轮细节返修）；Codex（或 `D4-A` 非作者研发替补）包办工程细节、全仓防漏与测试反作弊验真。
- **协作边界**：Markdown 排版与 Prettier 格式化交 `verdantflare-meta-markdown`；跨智能体交接单低 Token 校验交 `verdantflare-meta-agent`；图片受管入库交 `verdantflare-meta-googledrive`。目标仓库 `AGENTS.md` 有细化规则时叠加生效。

## 3. 按任务读取

| 当前任务或阶段特征                     | 读取专册                                     |
| :------------------------------------- | :------------------------------------------- |
| 判定计划等级、跨级迁移与核心目录边界   | [共享分类](references/01.base-category.md)   |
| 执行立项定级、脚手架、哈希与五档门禁   | [共享流程](references/01.base-workflow.md)   |
| 核查反自审、锁卷哈希与双棘轮防降级红线 | [共享规则](references/01.base-rules.md)      |
| 查阅退出码契约与 `PL-*` 规则码字典     | [共享验收](references/01.base-acceptance.md) |

按计划等级继续读取对应分支专册与脚手架模板：

- **简单计划分支（`C1.simple`）**：依次读取 [简单分类](references/C1.simple-category.md)、[简单流程](references/C1.simple-workflow.md)、[简单规则](references/C1.simple-rules.md) 与 [简单验收](references/C1.simple-acceptance.md)，脚手架见 [简单计划模板](resources/templates/plan-simple.template.md)。
- **复杂研发计划分支（`C2.complex`）**：依次读取 [复杂分类](references/C2.complex-category.md)、[复杂流程](references/C2.complex-workflow.md)、[复杂规则](references/C2.complex-rules.md) 与 [复杂验收](references/C2.complex-acceptance.md)，脚手架见 [复杂计划模板](resources/templates/plan-complex.template.md)、[研发评审模板](resources/templates/rd-review.template.md) 与 [研发验收模板](resources/templates/acceptance.template.md)。

## 4. 工具与命令

主检查器 [plan_lint.py](scripts/plan_lint.py)、全局安装器 [install.py](scripts/install.py) 与单测套件 [test_plan_lint.py](tests/test_plan_lint.py) 均使用 UTF-8 运行：

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
