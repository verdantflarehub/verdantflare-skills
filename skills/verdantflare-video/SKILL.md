---
name: verdantflare-video
description: 通过 VerdantFlare API 的公共模型 verdantflare-sd2，基于文本提示词以及本地图像、音频或视频参考素材生成 SD2 视频；当用户要求生成 VerdantFlare 视频或 SD2 产品/广告视频、查询视频任务状态、恢复已有任务或下载结果时使用。该技能会验证本地媒体文件，将其上传至配置的临时存储桶，创建一个异步任务，轮询任务状态并下载结果。
---

# VerdantFlare 视频

所有 API、对象存储、任务状态和下载操作均使用随技能提供的 Python 客户端。构造或解释 API 请求与响应时，阅读 `references/api.md`。

## 模型与渠道边界

- 始终通过 `/v1/videos` 使用公共模型 `verdantflare-sd2`。客户端已经固定该模型，不提供 `--model` 覆盖参数。
- 将供应商选择、上游模型映射、渠道锁定和计费视为 VerdantFlare API 的内部职责。不得把供应商渠道名、上游模型名、渠道 ID、Base URL 或供应商凭据暴露为技能参数，也不得绕过 VerdantFlare 直连供应商。
- 创建后只查询原公开 Task ID；渠道不可用、熔断或余额不足时停止并报告，不得换渠道重放、改用新的 UUID 或直接提交另一笔任务。
- 当前公开契约固定为 720P。不要添加分辨率选项或发送其他分辨率；需要开放新分辨率时必须先更新公共 API 契约、计价与客户端验证。

## 命令

将技能目录设为工作目录后运行脚本：

```text
macOS:  python3 scripts/video_client.py generate --prompt "..." [options]
        python3 scripts/video_client.py list
        python3 scripts/video_client.py resume <task-id>
        python3 scripts/video_client.py recover <client-request-id>
Windows: py -3 scripts/video_client.py generate --prompt "..." [options]
         py -3 scripts/video_client.py list
         py -3 scripts/video_client.py resume <task-id>
         py -3 scripts/video_client.py recover <client-request-id>
```

配置缺失或被拒绝时，使用 `scripts/` 中对应平台的包装脚本：macOS 使用 `install-config-macos.sh`，Windows 使用 `install-config-windows.ps1`。两个脚本都会调用 `install-config.py`，从内置的 VerdantFlare 引导地址下载配置，并在需要时下载固定版本的 `mc` 二进制文件。不得在命令参数、环境变量覆盖值、提示词、日志或任务记录中暴露下载的配置、API 密钥或 S3 密钥。

查找配置时，优先使用平台原生路径：Windows 为 `%LOCALAPPDATA%\\VerdantFlare\\Video\\.env`，macOS/Linux 为 `~/.config/verdantflare/video/.env`。在 WSL 中运行时，还需搜索挂载的 Windows 用户目录 `/mnt/<drive>/Users/<user>/AppData/Local/VerdantFlare/Video/.env`。如果找不到文件，报告搜索过的所有路径。如果找到多个文件，报告这些文件，并说明可通过进程级选项 `VERDANTFLARE_VIDEO_ENV_FILE` 明确指定配置。

使用本地媒体时，上传前先只读验证存储桶已有生命周期配置：必须存在已启用、精确覆盖当前安装专属上传前缀、在 7 天内到期的规则。无法读取、规则缺失或范围不精确时必须关闭失败，并明确说明未上传素材、未提交视频任务；客户端绝不新增、编辑或覆盖整桶生命周期配置。验证通过后，先持久化确定对象键和所有权证明，再上传文件并通过公开 URL 执行实际的媒体读取校验；只有确认返回内容与本地媒体类型一致后，才执行 `POST /videos`。纯文本生成无需执行上传步骤。

## 请求映射

- 确保提示词非空，并将其放入第一个文本内容项。
- 本地文件使用 `--image`、`--audio` 和 `--video`；已有 HTTPS URL 使用对应的 `--*-url` 选项。
- `--duration` 取值范围为 1 至 15 秒，默认为 10 秒。
- 画面比例使用 `16:9`、`9:16`、`1:1`、`4:3` 或 `3:4`，默认为 `16:9`。
- 除非用户明确要求静音，否则保持启用 `--generate-audio`。需要静音时使用 `--no-generate-audio`。
- v1 中最多提交 9 张图像、1 个音频参考和 3 个视频参考。每种媒体的本地文件和 HTTPS URL 合并计数。
- 保持收集到的视频顺序，使提示词可以使用 `video 1`、`video 2` 和 `video 3` 引用它们。
- 不要添加 `metadata.content`；客户端会发送 `references/api.md` 中定义的结构化 `messages` 格式。

## 恢复规则

- 创建前生成一个 UUID，同时作为 `Idempotency-Key` 和兼容的 `X-Request-ID`，两个 Header 必须完全相同。创建请求绝不自动重试。
- POST 成功后，必须先持久化任务，再开始轮询。遇到超时、连接失败、5xx 或 `409 submission_in_progress` 时，改查 `GET /video-submissions/{client_request_id}`，绝不再次 POST。
- 使用 `list` 显示本地任务与 Submission。使用 `resume <task-id>` 恢复已确认任务；使用 `recover <client-request-id>` 安全恢复不确定提交。恢复操作只执行 GET，不创建新任务。
- 原样保留 `PREPARED`、`SENDING`、`CONFIRMED`、`REJECTED` 和 `UNKNOWN`；不得把 `UNKNOWN` 猜成失败或排队。仅 `CONFIRMED` 可转为 Task ID 并继续轮询。
- 将 `queued` 和 `in_progress` 视为进行中；仅当存在 `metadata.url` 时，才将 `completed` 视为可下载；将 `failed` 和 `failure` 视为终态。
- 将未知状态或不含 URL 的已完成响应视为协议错误。
- 如果签名结果 URL 返回 403 或 410，可使用同一 Task ID 查询并刷新一次。只有精确匹配 VerdantFlare 同源结果代理的下载请求可携带 API Authorization；跨域或偏离该 content 路径的重定向不得携带。

本地上传对象使用私有清单逐对象管理。确定对象键后先以 `PENDING` 原子落盘，再执行上传，避免进程崩溃产生无清单孤儿。任务终态后尽快逐对象删除；存储桶生命周期规则负责不晚于 7 天的存储层到期，包括 `UNKNOWN` 和客户端不再启动的情况。客户端只能删除清单中通过所有权校验、位于当前安装专属前缀下的精确对象，不扫描或删除前缀，也不输出对象键或 `mc` 命令。

## 输出

返回公开的 VerdantFlare Task ID、最终公开状态、本地文件绝对路径、任务记录路径以及已知的结果元数据。对于未确认的 Submission，返回其原始状态和 `client_request_id`，优先使用 `recover <client-request-id>` 查询；`UNKNOWN` 仍需 API Center 对账，不能重新提交。

VerdantFlare 同源的 `/videos/{task-id}/content` 结果代理需要 Bearer；重定向时先移除继承的 Authorization，仅当目标仍严格匹配同一 API content 代理时重新添加。不得暴露上游供应商任务 ID、S3 对象名称、凭据、原始响应正文、错误信息中的签名 URL 查询参数或 `mc` 命令。
