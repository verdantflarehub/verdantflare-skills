---
name: verdantflare-video
description: 为未选模型的视频制作选择路线，或通过 VerdantFlare API 生成、恢复和下载明确指定的 SD2 视频。
---

# VerdantFlare Video

- 用户未选模型：默认 Sol-H3，读取 [H3 入口](../verdantflare-video-h3/SKILL.md)，根据需求准备输入；不自动提交缺少冻结输入的任务。
- 明确 H3 或原版：同一 H3 入口，尊重路线选择。宿主不支持时报告具体能力缺口，不静默改用 SD2。
- 明确 SD2，或恢复已有 SD2 任务：读取 [SD2 工作流](references/sd2-workflow.md)；只有构造/解释请求时再读 [API 契约](references/api.md)。使用随附客户端，命令参数可通过 `python3 scripts/video_client.py --help` 查询。
- 已有任务：沿原 API/MCP 和任务标识恢复；若记录不足以确定来源，先查询，不按当前默认模型重新生成。
- 配置问题：读取 [环境说明](../ENVIRONMENT.md)。若独立安装缺少 H3 Skill，明确报告该依赖；不把 SD2 客户端当作 H3 客户端。

本地准备、结果下载、检查和修复在授权范围内自主执行。SD2 提交不自动重试，未知提交只查询恢复；本地媒体上传前验证生命周期，删除仅限所有权清单中的精确对象。凭据不泄漏到重定向或日志。

完成所请求的生成/恢复/下载、读取真实状态并验证结果后交付任务 ID、文件与记录。外部阻塞时保留可恢复状态，不用新的提交绕过失败或重复收费。
