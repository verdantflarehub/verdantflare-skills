---
name: verdantflare-higgsfield
description: 用户明确请求通过团队 Higgsfield 会员生成视频、首帧动画或恢复已有 Higgsfield 视频任务时使用。官方视频工作流的 VerdantFlare 适配版，统一委托自有 verdantflare-video，通过 Studio 调用服务器官方 CLI。
---

# Higgsfield 团队视频

## 1. 入口与依赖

这是 Higgsfield Skills 0.13.0 的视频生成流程适配，不是官方全功能插件。固定来源、许可及修改范围见包根目录 SOURCE.md 和 LICENSE。只处理视频，图片、音频、Soul、营销工作流不自动转为视频调用。

先定位用户的 verdantflare-skills 仓库并读取其中 skills/verdantflare-video/SKILL.md 与 references/higgsfield.md。插件命令桥 scripts/video.py 在源码仓库内自动定位；插件被宿主复制到缓存时使用 VERDANTFLARE_SKILLS_ROOT 环境变量指向该仓库。缺依赖时说明缺口并保留输入，不安装全局 hf 或从插件目录寻找账号文件。

## 2. 生成与恢复

1. 通过命令桥 check 发现实际 Studio Video 工具、宿主能力及账号渠道配置。用户指定模型保持不变；在 Higgsfield 渠道未指定时采用自有 Skill 的 Seedance 2.5 默认，当前开放组合以 capabilities 为准。
2. 按场景、主体、动作、镜头、光线和预期时长组织提示词；首帧仅在实际模型支持时使用本项目的受控 Artifact 引用。输入本机图片先经 Video 导入流程，不传供应商 URL 或服务器路径。
3. 使用命令桥 generate，显式提供 project-id、固定 idempotency-key、prompt、duration 及模型。命令桥固定 route=higgsfield，参数验证和提交由自有 Video Skill 完成。报价和真实消费遵守当前用户预算，缺关键输入才集中澄清。
4. 保存 Video task ID，使用原任务 status/result/resume。超时、未知提交和归档失败保持原身份，不创建新尝试，不切换账号、渠道或模型。
5. 完成后交付受控 ContentRef 与可读结果，简述模型、时长和用途，不展示账号 Token、供应商签名 URL 或内部日志。技术校验不代表用户创作批准。

## 3. 账号维护

成员使用既有 Studio MCP Token，不登录共享会员。管理员的官方 CLI 固定版本安装在自有 Video Skill 私有运行时，登录、交接和对账依其 higgsfield.md 执行。服务器经固定 DMIT 出口调用官方 CLI 并刷新 PVC 凭据；插件包不包含二进制、OAuth 状态、授权文件或自动登录 hook。
