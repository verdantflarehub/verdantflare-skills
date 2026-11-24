---
name: verdantflare-meta-skill
description: 设计、评审、新建和重构可复用技能，规定以扁平前缀原子能力入口（NN.能力名.entrypoint.md）为主轴的统一结构、按需共享公共专册（00.common.*.md）与体积上限，把已验证经验沉淀为有适用边界的操作指引，并提供脚手架与静态检查工具。用于“评审技能”“新建技能”“改造/通用化技能”“整改技能结构”“把经验沉淀到技能”等请求；普通业务任务不因此转为技能改造。
---

# 通用技能设计与质量评审（verdantflare-meta-skill）

## 1. 范围与边界

本技能规定所有技能的统一结构并负责设计、新建、整改、评审与安装：入口 `SKILL.md` 只做原子能力路由，每个原子能力由 `references/NN.<capability>.entrypoint.md` 统领（输入与输出 → 分类 → 工作流 → 规范 → 验收），跨能力共享内容按需写入 `references/00.common.*.md`。评审请求只输出发现与建议，修订请求才改目标技能；复用已有授权，全局安装与发布按用户实际授权执行。

`skill-creator`、`beagle-skills-creator` 等可作为可选协作对象，只以名称提及、不建立链接；Markdown 排版交由目标环境的 Markdown 治理技能（如 `verdantflare-meta-markdown`）。用户明确要求与目标仓库规则优先于本技能默认建议。

## 2. 能力清单与按任务读取

按要执行的原子能力直达对应入口：

| 原子能力 | 入口文档                                                          |
| :------- | :---------------------------------------------------------------- |
| 架构设计 | [01.design.entrypoint.md](references/01.design.entrypoint.md)     |
| 新建技能 | [02.create.entrypoint.md](references/02.create.entrypoint.md)     |
| 整改沉淀 | [03.refactor.entrypoint.md](references/03.refactor.entrypoint.md) |
| 评审验收 | [04.review.entrypoint.md](references/04.review.entrypoint.md)     |
| 安装接入 | [05.install.entrypoint.md](references/05.install.entrypoint.md)   |

先读目标能力的 `entrypoint.md`，无需通读其他能力；跨能力公共约束见 [00.common.rules.md](references/00.common.rules.md)（由各能力入口引用）。

## 3. 工具与命令

[lint_skill.py](scripts/lint_skill.py) 负责初始化合规骨架与静态检查，依赖见 [requirements.txt](scripts/requirements.txt)，回归测试为 [test_lint_skill.py](scripts/test_lint_skill.py)，模板位于 `resources/templates/`。命令在本技能安装目录运行，完整参数见对应能力入口。

```text
python -X utf8 scripts/lint_skill.py init /path/to/new-skill --capabilities 01.t2v,02.i2v --common rules
python -X utf8 scripts/lint_skill.py lint /path/to/target-skill --style numbered
python -X utf8 -m unittest discover -s scripts -p test_lint_skill.py
```

技能源码只在一处维护，安装副本不直接修改，统一安装到 `~/.agents/skills/<name>/`。
