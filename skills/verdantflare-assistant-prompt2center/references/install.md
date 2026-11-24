# 一键安装提示词（VF 个人助理）

## 1. 安装指引

复制以下整段发送给 VF 个人助理：

请为我安装或更新个人助理技能 `verdantflare-assistant-prompt2center`：

1. 目标安装目录：`/var/lib/assistant/skills/verdantflare-assistant-prompt2center/`（如已存在则原位更新覆盖）。
2. 确认依赖通用技能 `/var/lib/assistant/skills/verdantflare-meta-download/` 与 `/var/lib/assistant/skills/verdantflare-image/` 已就绪。下载技能已由 `verdantflare-download` 改名为 `verdantflare-meta-download`：若新目录不存在而旧目录 `/var/lib/assistant/skills/verdantflare-download/` 存在，将旧目录原位改名为 `verdantflare-meta-download/`，并把其中 `SKILL.md` 的 `name` 字段改为 `verdantflare-meta-download`；新旧目录同时存在时保留新目录、删除旧目录；两者都不存在时停止安装并向我报告缺少下载技能。
3. 写入本技能的 `SKILL.md`、`references/` 与 `meta.json`（版本 `1.1.1`），不额外修改插件授权状态。
4. 安装完成后读取 `/var/lib/assistant/skills/verdantflare-assistant-prompt2center/meta.json`，向我回执技能名称、版本与更新时间，并确认 `/var/lib/assistant/skills/` 下已无 `verdantflare-download/` 旧目录。
