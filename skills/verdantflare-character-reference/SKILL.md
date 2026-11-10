---
name: verdantflare-character-reference
description: 兼容旧名称的角色参考库技能入口；显式调用旧名时转到verdantflare-character-face，新增锁脸任务使用新技能。
---

# 角色参考库兼容入口

## 1. 迁移与依赖

完整流程已迁入[verdantflare-character-face](../verdantflare-character-face/SKILL.md)。读取新入口后按当前任务阶段执行；本包不维护另一套生产规则。安装本兼容入口时须一并安装新技能及对应阶段依赖，缺少新技能时报告依赖缺失，不恢复旧副本流程。名称迁移不授权生成、入库或发布。
