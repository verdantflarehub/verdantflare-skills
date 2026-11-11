---
name: verdantflare-meta-skill
description: 设计、评审、新建和重构可复用技能，规定所有技能统一的入口结构、四份底座专册（分类、流程、规则、验收）与体积上限，把已验证经验沉淀为有适用边界的操作指引，并提供脚手架与静态检查工具。用于“评审技能”“新建技能”“改造/通用化技能”“整改技能结构”“把经验沉淀到技能”等请求；普通业务任务不因此转为技能改造。
---

# 通用技能设计与质量评审（verdantflare-meta-skill）

## 1. 范围与边界

本技能规定所有技能的统一结构并负责评审、新建、整改与经验沉淀：入口 `SKILL.md` 只做路由，规范、流程、验收等正文一律写进 `references/` 下的四份底座专册，入口超限或缺专册视为不合格。评审请求只输出发现与建议，修订请求才改目标技能；复用已有授权，全局安装与发布按用户实际授权执行。

`skill-creator`、`beagle-skills-creator` 等可作为可选协作对象，只以名称提及、不建立链接；Markdown 排版交由目标环境的 Markdown 治理技能（如 `verdantflare-meta-markdown`）。用户明确要求与目标仓库规则优先于本技能默认建议。

## 2. 按任务读取

| 当前任务                         | 读取资料                                     |
| :------------------------------- | :------------------------------------------- |
| 统一结构、文件划分与分支矩阵     | [结构与分类](references/01.base-category.md) |
| 新建、整改存量技能与沉淀经验     | [执行流程](references/01.base-workflow.md)   |
| 入口与专册内容归属、体积与写法   | [规则约束](references/01.base-rules.md)      |
| 静态检查、行为验证与评分门禁设计 | [验收标准](references/01.base-acceptance.md) |
| 全局安装或本机智能体接入         | [安装与接入](references/install.md)          |

先读当前任务对应专册；执行某个分支时只加载共享约束与该分支资料。

## 3. 工具与命令

[lint_skill.py](scripts/lint_skill.py) 负责初始化合规骨架与静态检查，依赖见 [requirements.txt](scripts/requirements.txt)，回归测试为 [test_lint_skill.py](scripts/test_lint_skill.py)，入口模板为 [SKILL.template.md](resources/templates/SKILL.template.md)。命令在本技能安装目录运行，参数含义与完整用法见执行流程专册。

```text
python -X utf8 scripts/lint_skill.py init /path/to/new-skill [--categories C1.local,C2.remote]
python -X utf8 scripts/lint_skill.py lint /path/to/target-skill [--style numbered]
python -X utf8 -m unittest discover -s scripts -p test_lint_skill.py
```

技能源码只在一处维护，安装副本不直接修改，统一安装到 `~/.agents/skills/<name>/`。
