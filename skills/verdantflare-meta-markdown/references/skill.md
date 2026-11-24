# 技能文档规范

## 1. 职责与结构

技能文档由入口 `SKILL.md` 与按需加载的 `references/` 专册组成。入口固定为范围与边界、能力清单与按任务读取、工具与命令三节；每个原子能力由扁平前缀入口 `references/NN.<capability>.entrypoint.md` 统领（输入与输出 → 分类 → 工作流 → 规范 → 验收），篇幅超长时按需拆出同前缀子专册 `NN.<capability>.{category,workflow,rules,acceptance}.md`，跨能力共享内容按需写入 `00.common.{category,workflow,rules,acceptance}.md`。目录契约与体积上限以技能设计元技能（如 `verdantflare-meta-skill`）为准，本专册只管排版。

`SKILL.md` 的 front matter 必须包含 `name` 与 `description`。`description` 说明提供哪些原子能力、何时触发、不做什么，使宿主能据此选择技能；不写实现细节。

## 2. 写作要求

- 入口路由表最多两列：左列用简短动宾短语逐项列出本技能提供的原子能力（一活一行），右列直链对应 `references/NN.<capability>.entrypoint.md`（或按需追加 `00.common.*.md` 与独立主题专册），禁止仅罗列抽象维度名称交差。
- 每条规则说明适用范围与例外；用户偏好可直接记录，工具行为与算法效果须实测后才写为事实。
- 命令写可复制的最短形式，说明工作目录；全局技能不假定目标仓库包含技能目录。
- 全局安装的技能不链接源码仓库的业务资产，仓库专属契约留在该仓库规则中。

## 3. 验收

修改后执行目标环境已有的技能静态检查与脚本测试，再按通用流程执行 Format 与结构检查。设计、评审或重构技能时，若环境中存在技能设计元技能（如 `verdantflare-meta-skill`），同时遵循其方法论。
