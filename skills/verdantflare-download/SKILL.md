---
name: verdantflare-download
description: 通用 HTTP(S) 下载与资料研究。用户要求下载文件、保存网页、图片、音视频、文档、数据集或压缩包，或给出资源链接、平台分享口令要求总结、分析、拆解或研究时自动触发。下载后按实际文件类型读取并引用证据。
---

# 通用下载与资料研究（verdantflare-download）

## 1. 范围与边界

[通用入口](scripts/download.py) 保存 HTTP(S) 响应的原始内容，支持网页、文档、数据、压缩包、图片和音视频。已知媒体平台的分享页走专用解析器，其他地址默认保存页面或文件。安装 Python 3.10+、httpx、PyYAML 和 yt-dlp；视频抽帧和测量需要 ffmpeg、ffprobe。

## 2. 按任务读取

| 当前任务                      | 读取资料                                     |
| :---------------------------- | :------------------------------------------- |
| 协议取证与 `C1~C3` 分类路由   | [全局分类](references/01.base-category.md)   |
| 三阶段下载主干流程与 CLI 参数 | [全局流程](references/01.base-workflow.md)   |
| 物理不变量、否决红线与百分制  | [全局规则](references/01.base-rules.md)      |
| 前置测量与 `Gate G1~G4` 门禁  | [全局验收](references/01.base-acceptance.md) |

分析或研究请求先读[下载与研究流程](references/research.md)，用全局安装目录中的脚本执行，再查看实际画面或正文并回答。

分支专册按 `classify_platform_and_category()` 输出的分类代号按需加载：

- **`C1`（抖音与字节系无水印原画）**：[分类](references/C1.douyin-media-category.md)、[流程](references/C1.douyin-media-workflow.md)、[规则](references/C1.douyin-media-rules.md)、[验收](references/C1.douyin-media-acceptance.md)
- **`C2`（小红书与快手状态树直解）**：[分类](references/C2.xhs-kuaishou-category.md)、[流程](references/C2.xhs-kuaishou-workflow.md)、[规则](references/C2.xhs-kuaishou-rules.md)、[验收](references/C2.xhs-kuaishou-acceptance.md)
- **`C3`（B站/微博/X/YouTube/通用）**：[分类](references/C3.bili-weibo-global-category.md)、[流程](references/C3.bili-weibo-global-workflow.md)、[规则](references/C3.bili-weibo-global-rules.md)、[验收](references/C3.bili-weibo-global-acceptance.md)

## 3. 工具与命令

| 脚本文件                                                           | 职责简称                           |
| :----------------------------------------------------------------- | :--------------------------------- |
| [mk_video_download.py](scripts/mk_video_download.py)               | 总控 CLI 调度、解析与流式落盘      |
| [video_download_evaluator.py](scripts/video_download_evaluator.py) | `ffprobe` 测量与 `Gate G1~G4` 评测 |

- `mk_video_download.py`：口令提链取证、`C1 ~ C3` 无水印最高码率解析、流式落盘、首帧提取与 VP9 预览转码。
- `video_download_evaluator.py`：`ffprobe` 容器轨道体检、`Gate G1 ~ G4` 门禁校验与 100 分制客观量化打分。
