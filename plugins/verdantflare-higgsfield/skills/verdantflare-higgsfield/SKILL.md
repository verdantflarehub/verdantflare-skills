---
name: verdantflare-higgsfield
description: 通过团队 Higgsfield 会员生成或恢复视频，包括 Seedance 2.5 Draft 的 R2V／ref2va 多图加视频参考生成。官方视频工作流的 VerdantFlare 适配版，统一委托自有 verdantflare-video，通过 Studio 调用服务器官方 CLI。
---

# Higgsfield 团队视频

## 1. 入口与依赖

这是 Higgsfield Skills 0.13.0 的视频生成流程适配，不是官方全功能插件。固定来源、许可及修改范围见包根目录 SOURCE.md 和 LICENSE。只处理视频，图片、音频、Soul、营销工作流不自动转为视频调用。

先读取已安装的 Studio MCP 版 verdantflare-video（v0.5.0 起）及其 references/higgsfield.md。插件命令桥 scripts/video.py 自动查找 CODEX_HOME/skills、用户 .codex/skills 或 .agents/skills，以及当前项目的技能目录；也兼容 VERDANTFLARE_SKILLS_ROOT 指向已更新的自有 Skills 仓库。命令桥检查 Studio 渠道契约标识，拒绝同名公共 API 版或旧版脚本。成员独立安装兼容 Video Skill 即可，不需要开发工作区。缺依赖时说明缺口并保留输入，不安装全局 hf 或从插件目录寻找账号文件。

## 2. 生成与恢复

1. 通过命令桥 check 发现实际 Studio Video 工具、宿主能力及账号渠道配置。保留用户指定模型；当前宿主未声明本渠道默认模型，新提交须明确 `--model`，不自动选择 Seedance 2.5 或 SD2。用户选择 Wan 3.0／480p 时据此匹配能力；官网促销不等同于 CLI 零报价，也不等同于已启用 Unlimited。
2. 按场景、主体、动作、镜头、光线和预期时长组织提示词；首帧仅在实际模型支持时使用本项目的受控 Artifact 引用。输入本机图片先经 Video 导入流程，不传供应商 URL 或服务器路径。
3. 使用命令桥 generate，显式提供 project-id、固定 idempotency-key、prompt、duration 及模型。命令桥固定 route=higgsfield，参数验证和提交由自有 Video Skill 完成。报价和真实消费遵守当前用户预算，缺关键输入才集中澄清。
4. 保存 Video task ID，使用原任务 status/result/resume。超时、未知提交和归档失败保持原身份，不创建新尝试，不切换账号、渠道或模型。
5. 完成后交付受控 ContentRef 与可读结果，简述模型、时长和用途，不展示账号 Token、供应商签名 URL 或内部日志。技术校验不代表用户创作批准。

### 2.1 Seedance 2.5 Draft × Higgsfield

用户指定 Draft／ref2va 时使用 `--model seedance_2_5_draft --resolution 480p`，对应 R2V／`reference_to_video`。当前支持 4–15 秒、最多 4 张参考图＋1 条参考视频；分别重复传入 `--image-ref <Artifact ID>=identity` 和 `--video-ref <Artifact ID>=motion`，按实际素材职责组织提示词。不能替换为普通 2.5、T2V 或 first_frame／last_frame。完整素材预检会查询报价；Draft 不代表免费或 Unlimited。团队额度不足时由管理员在 Higgsfield 应用调整限额，不能绕过限额或重复提交。

成员可直接说：“使用 Seedance 2.5 Draft × Higgsfield，以当前项目的角色参考图和动作参考视频生成 4 秒、480p 的 R2V，保留原任务用于恢复。”沿用实际授权和素材；此示例不自动批准消费。

## 3. 账号维护

成员使用既有 Studio MCP Token，不登录共享会员。管理员的官方 CLI 固定版本安装在自有 Video Skill 私有运行时，登录、交接和对账依其 higgsfield.md 执行。服务器经固定 DMIT 出口调用官方 CLI 并刷新 PVC 凭据；插件包不包含二进制、OAuth 状态、授权文件或自动登录 hook。
