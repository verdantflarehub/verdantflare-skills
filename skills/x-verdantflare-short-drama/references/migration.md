# 短剧 Skill 迁移

## 1. 旧名称与新模块

统一安装和调用 `x-verdantflare-short-drama`。下表保留旧名称以便定位历史资料，不是第二套注册入口。

| 原名称后缀（前缀为 x-verdantflare-short-drama-） | 新模块 |
| --- | --- |
| script | [编剧](writing/guide.md) |
| script-review | [审核](review/guide.md) |
| director | [导演](directing/guide.md) |
| film-production | [制作](production/guide.md) |
| character-performance | [表演](performance/guide.md) |
| local-scene-breakdown | [本机拆解](local-breakdown/guide.md) |
| commercial-production | [广告适配](commercial/guide.md) |

## 2. 路径与兼容边界

原模块 `references/<文件>` 改为 `x-verdantflare-short-drama/references/<模块>/<文件>`；原 `SKILL.md` 的执行说明改为该模块的 `guide.md`。只有包根保留 Skill frontmatter 与 UI 元数据，避免自动发现时出现重复能力。

导演校验器和本机拆解脚本迁到包根 `scripts/`；测试迁到包根 `tests/`。脚本参数、计划 JSON 契约、拆解输出契约均不变。迁移后从包根执行命令，不从 references 子目录执行。

已安装的旧包、历史项目和外部文档不会被本次仓库整理自动升级。需要更新客户端安装和旧路径引用；不自动删除用户机器上的安装。本仓库现行目录与包测试使用新入口。项目正文和已批准素材内容不因路径迁移改版。

## 3. 维护说明

新增短剧能力优先放到已有阶段；只有独立交付且会显著改变调用边界时才增加模块。模块不写 `SKILL.md` 或独立 UI 元数据；共享规则由根入口或唯一规范维护，细节按需读取，避免再次拆成多个竞争入口。
