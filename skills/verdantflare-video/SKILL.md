---
name: verdantflare-video
description: 通过 VerdantFlare API，基于文本提示词以及本地图像、音频或视频参考素材生成 SD2 视频；当用户要求生成 VerdantFlare 视频或 SD2 产品/广告视频、查询视频任务状态、恢复已有任务或下载结果时使用。该技能会验证本地媒体文件，将其上传至配置的临时存储桶，创建一个异步任务，轮询任务状态并下载结果。
---

# VerdantFlare 视频

所有 API、对象存储、任务状态和下载操作均使用随技能提供的 Python 客户端。构造或解释 API 请求与响应时，阅读 `references/api.md`。

## 命令

将技能目录设为工作目录后运行脚本：

```text
macOS:  python3 scripts/video_client.py generate --prompt "..." [options]
        python3 scripts/video_client.py list
        python3 scripts/video_client.py resume <task-id>
Windows: py -3 scripts/video_client.py generate --prompt "..." [options]
         py -3 scripts/video_client.py list
         py -3 scripts/video_client.py resume <task-id>
```

配置缺失或被拒绝时，使用 `scripts/` 中对应平台的包装脚本：macOS 使用 `install-config-macos.sh`，Windows 使用 `install-config-windows.ps1`。两个脚本都会调用 `install-config.py`，从内置的 VerdantFlare 引导地址下载配置，并在需要时下载固定版本的 `mc` 二进制文件。不得在命令参数、环境变量覆盖值、提示词、日志或任务记录中暴露下载的配置、API 密钥或 S3 密钥。

查找配置时，优先使用平台原生路径：Windows 为 `%LOCALAPPDATA%\\VerdantFlare\\Video\\.env`，macOS/Linux 为 `~/.config/verdantflare/video/.env`。在 WSL 中运行时，还需搜索挂载的 Windows 用户目录 `/mnt/<drive>/Users/<user>/AppData/Local/VerdantFlare/Video/.env`。如果找不到文件，报告搜索过的所有路径。如果找到多个文件，报告这些文件，并说明可通过进程级选项 `VERDANTFLARE_VIDEO_ENV_FILE` 明确指定配置。

使用本地媒体时，先将文件上传至配置的 S3 兼容存储桶，再执行 `POST /videos`。存储桶缺失或无法访问时，将其报告为对象存储预配或配置错误，并明确说明尚未提交视频任务。纯文本生成无需执行上传步骤。

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

- POST 成功后，必须先持久化任务，再开始轮询。遇到超时、连接失败、5xx 或响应格式错误时，绝不再次提交 POST。
- 使用 `list` 显示本地记录。使用 `resume <task-id>` 查询并下载已有任务；恢复操作绝不创建新任务。
- 将 `queued` 和 `in_progress` 视为进行中；仅当存在 `metadata.url` 时，才将 `completed` 视为可下载；将 `failed` 和 `failure` 视为终态。
- 将未知状态或不含 URL 的已完成响应视为协议错误。
- 如果签名结果 URL 返回 403 或 410，可使用同一 Task ID 查询并刷新一次。绝不向结果文件所在主机发送 API Authorization 请求头。

## 输出

返回公开的 VerdantFlare Task ID、最终公开状态、本地文件绝对路径、任务记录路径以及已知的结果元数据。对于状态为 `PENDING` 或 `UNKNOWN` 的提交，使用其 `client_request_id` 说明情况，并告知用户在再次创建任务前先前往 VerdantFlare API Center 核实。

不得暴露 JD 上游任务 ID、S3 对象名称、凭据、原始响应正文、错误信息中的签名 URL 查询参数或 `mc` 命令。
