---
name: verdantflare-meta-skill
description: 设计、新建、整改、评审和安装可复用技能，规定以扁平前缀原子能力入口（NN.能力名.entrypoint.md）为主轴的统一结构、按需共享公共层（00.common.*.md），并按电影工业四层分型（原子技能 atomic、工艺技能 craft、专员技能 crew、导演技能 director）及四层之外的工具技能（元治理 meta、文档编写 write、助手调度 agent）实施分层约束、三段式命名与静态检查。用于“评审技能”“新建技能”“改造/通用化技能”“整改技能结构”“安装技能”“把经验沉淀到技能”等请求；普通业务任务不因此转为技能改造。
---

# 通用技能设计与质量评审（verdantflare-meta-skill）

## 1. 范围与边界

本技能规定所有技能的统一扁平前缀结构（`SKILL.md` 路由 + `references/NN.<capability>.entrypoint.md` 五段式闭环 + 按需 `00.common.*.md` 公共层），并按四层技能分型及三类工具技能分型负责安装、设计、新建、整改、经验沉淀与质量评审。评审请求只输出发现与建议，修订请求才改目标技能；复用已有授权，全局安装与发布按用户实际授权执行。

`skill-creator`、`beagle-skills-creator` 等可作为可选协作对象，只以名称提及、不建立链接；Markdown 排版交由 `verdantflare-write-markdown`。用户明确要求与目标仓库规则优先于本技能默认建议。

## 2. 能力清单与按任务读取

按目标技能分型或安装需求直达对应入口：

| 原子能力 | 入口文档                                                          |
| :------- | :---------------------------------------------------------------- |
| 安装技能 | [01.install.entrypoint.md](references/01.install.entrypoint.md)   |
| 原子技能 | [02.atomic.entrypoint.md](references/02.atomic.entrypoint.md)     |
| 工艺技能 | [03.craft.entrypoint.md](references/03.craft.entrypoint.md)       |
| 专员技能 | [04.crew.entrypoint.md](references/04.crew.entrypoint.md)         |
| 导演技能 | [05.director.entrypoint.md](references/05.director.entrypoint.md) |
| 元治理   | [06.meta.entrypoint.md](references/06.meta.entrypoint.md)         |
| 文档编写 | [07.write.entrypoint.md](references/07.write.entrypoint.md)       |
| 助手调度 | [08.agent.entrypoint.md](references/08.agent.entrypoint.md)       |

先读目标能力入口，无需通读其他能力；跨能力公共规范、通用流程与验收基准见 [00.common.rules.md](references/00.common.rules.md)、[00.common.workflow.md](references/00.common.workflow.md) 与 [00.common.acceptance.md](references/00.common.acceptance.md)（由各入口引用）。

## 3. 工具与命令

[lint_skill.py](scripts/lint_skill.py) 负责初始化合规骨架与分型静态检查，依赖见 [requirements.txt](scripts/requirements.txt)，回归测试为 [test_lint_skill.py](scripts/test_lint_skill.py)，模板位于 `resources/templates/`。

```text
python -X utf8 scripts/lint_skill.py init /path/to/new-skill --capabilities 01.t2v,02.i2v --common rules
python -X utf8 scripts/lint_skill.py lint /path/to/target-skill --style numbered [--profile craft|crew|director|meta|write|agent]
python -X utf8 -m unittest discover -s scripts -p test_lint_skill.py
```

技能源码只在一处维护，安装副本不直接修改，统一安装到 `~/.agents/skills/<name>/`。
