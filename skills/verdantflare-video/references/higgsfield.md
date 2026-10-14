# Higgsfield 会员渠道与官方 Skills 适配

## 1. 团队视频流程

本流程适配官方 `higgsfield-generate` 0.13.0 的视频模型发现、参数组织和恢复步骤；来源为 [官方 Skills 固定提交](https://github.com/higgsfield-ai/skills/tree/f83af0bc1d937c8119099a11f8ebbf5e6fb99819)，许可见 [MIT 原文](higgsfield-LICENSE.txt)。官方 CLI 固定为 1.1.26，运行时归本 Skill 的 `.runtime/` 私有目录，不要求全局安装。

先发现 Studio `video.capabilities/create/preflight/status/result`，按 `route=higgsfield` 和目标 `model` 同时匹配能力；只在 configured、enabled 且目标组合可用时提交。模型、时长、画幅、分辨率以宿主声明为准，官方完整模型目录不等于团队入口全部开放。

使用 `scripts/video_client.py generate --route higgsfield --model <宿主模型> --project-id <项目> --idempotency-key <固定尝试键> --prompt <提示词> --duration <秒数> --resolution 720p`。参数和素材通过现有 Video Artifact 导入；支持首帧时使用本项目 `--image-ref <Artifact ID>=first_frame`，不把本机路径或供应商上传 ID 当团队 Artifact。

官方默认的全局安装、本地生成、`--wait` 和供应商 URL 交付在这里改为私有运行时、Studio 异步任务和中央 Artifact 固定 ContentRef。团队调用使用已有 MCP Token，不要求成员登录 Higgsfield，不将会员 Token 放入请求。模型或渠道失败不自动切换。

宿主声明 `wan3_0` 的 `reference_to_video` 后，可重复传 `--image-ref <Artifact ID>=<用途>` 和 `--video-ref <Artifact ID>=<用途>`，显式指定 `--resolution 480p --ratio 9:16`。数量按宿主 `reference_limits` 校验，不混用 `first_frame/last_frame`。Skill 的预检通过同一 Studio 查询完整素材报价；多素材由服务器受控上传并持久复用供应商回执。官网促销、CLI 报价和实际扣费分别核验，不能假定 Wan 免费，也不自动换成其他模型。提示词说明有序图片与视频职责，不默认照搬 H3 标签语法。

## 2. 恢复与费用

持久保存项目、route、model、幂等键和 Video task ID。`submission_unknown` 表示可能已经扣费，继续查询原任务并对账，不能重复 create。远端完成但 Artifact 尚未登记时仍等待归档恢复；只在固定内容校验及受控回读完成后交付。`media_contract_mismatch` 表示供应商实际输出不符合请求规格，保留原任务与已消费额度，不自动重新生成。

服务报价与实际流水分开记录，不把 quoted_credits 当实际扣费。费用及条数遵循用户已授权上限，先查可用能力和报价，不因为官方 Skill 默认建议而跳过预算约束。技术成功不替代创作审核。

任务完成后使用 `scripts/video_client.py result <Video task ID> --output <目标.mp4>`，经 Studio `artifact.read` 和受控内容路径下载固定版本，核对 Artifact 与 Video 两端摘要及大小。已有不同内容不覆盖；失败恢复原任务，不重新生成。插件命令桥接受相同 `result --output` 参数。

## 3. 管理员私有 CLI

`python scripts/higgsfield.py install` 下载官方固定版本并校验摘要；`version` 验证私有二进制。Windows 与 Linux amd64 支持，其他平台明确拒绝，不隐式全局安装。

登录前确保本地浏览器与 CLI 和服务器使用同一 DMIT 出口。5090 DMIT 端点固定为 `openclash.openclash.svc.cluster.local:1081`；本地可由管理员保持 `kubectl --context dev.verdantflarehub.com -n openclash port-forward service/openclash 19081:1081`，CLI 的 `--proxy http://127.0.0.1:19081` 经该通道出网。浏览器按已配置的 DMIT Proxifier 规则或相同受控代理核验，OAuth localhost 回调 Direct；代理故障不回退。

使用 `login --proxy <DMIT入口>`、`workspace <workspace_id> --proxy <DMIT入口>`、`account --proxy <DMIT入口>`。脚本只输出脱敏状态，不开放 `auth token` 或本地收费生成。真实授权状态保存在本 Skill `.runtime/higgsfield/auth/`，不会进入 Git。

授权交接使用 `handover --pod <受控导入Pod> --container <容器> --workspace <workspace_id>`，通过已认证 Kubernetes exec stdin 导入服务器 PVC。导入 Pod 使用网关镜像并挂载同一 PVC，服务必须先停止且成功取得独占锁；在线网关持锁时导入明确失败。更新凭据需提供受控的 `--expected-revision`，不覆盖未知新版本。

成功交接后本地标记为服务器托管，禁止继续使用本地旧 Refresh Token；登录新授权会明确开始新一轮交接。网关刷新文件与镜像分离，回滚不回写旧凭据。成员业务身份与管理员 OAuth 登录是两套职责，不增加成员调用审批。

交接前先保存本地 pending 标记；断线或响应未知时也停止本地刷新，管理员运行 `reconcile --pod <服务器Pod> --container <容器>`，按原 revision 对账；此命令只取脱敏回执，不读取或刷新账号。若导入在凭据保存后、回执落盘前中断，先停止网关，由同 PVC 导入 Pod 在独占锁下修复回执；不重传旧凭据。重新登录使用独立空授权目录获得新浏览器授权，成功后再替换本地状态，不使用服务器托管的旧副本刷新。

## 4. 验证边界

本 Skill 的流程适配、私有 CLI 安装与真实宿主插件发现分别验收。官方 Skills 插件完整快照在临时研究中已安装；不能因为本 reference 存在就宣称宿主插件加载或团队调用已经通过。
