# Grok 视频调用

## 1. 发现与提交

先发现 `video.capabilities`、`video.create`、`video.status`、`video.result`。显式选择 `route=grok`，仅在对应能力已启用且配置完整时提交，模型取该渠道返回的 `model`。

文生视频使用 `mode=text_to_video`、空 `references`；图生视频使用 `mode=image_to_video`，且只接受同一项目的一张 `images` 引用，`purpose=first_frame`。省略图生视频的画幅即可继承原图；显式画幅必须匹配。图像按现行 Video 导入协议登记。

适配器支持 1–15 秒整数时长、`480p/720p`。传入 `resolution`，不传 `quality_profile`、种子、音频开关或 H3 专属字段。提示词保持用户意图，不添加 H3 的素材编号标签。

CLI 使用 `generate --route grok --project-id <项目> --idempotency-key <固定尝试键> --prompt <提示词> --duration 5 --resolution 720p`；图生视频另传 `--image-ref <Artifact ID>=first_frame`。省略 `--model` 使用宿主渠道模型。

## 2. 恢复与验收

持久保存原模型、渠道、项目、尝试键和任务 ID。`submission_unknown` 不新建收费尝试，也不切换渠道；查询原任务并核对供应商记录。`collect_retry` 仅恢复结果回收，远端完成但文件尚未回收不能报告成功。

读取实际 MIME、SHA-256、宽高、时长、FPS 和音轨信息；不强制 H3 的 24 FPS，不虚构音频或费用。当前适配器最长观测 24 小时；超限终止本次观测，不自动重提。端点或凭据变更时旧任务暂停，恢复原配置后再查询；不向新端点发送旧任务 ID。

凭据仅由服务端 Secret 提供。技术完成不代表用户艺术审核通过。
