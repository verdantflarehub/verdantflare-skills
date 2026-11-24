---
name: verdantflare-download
description: 通用 HTTP(S) 下载与资料研究。用户要求下载文件、保存网页、图片、音视频、文档、数据集或压缩包，或给出资源链接、平台分享口令要求总结、分析、拆解或研究时自动触发。下载后按实际文件类型读取并引用证据。
---

# 通用下载与资料研究（verdantflare-download）

## 1. 范围与边界

[通用入口](scripts/download.py) 保存 HTTP(S) 响应的原始内容，支持网页、文档、数据、压缩包、图片和音视频；已知媒体平台的分享页与口令走专用无水印解析器，其他地址默认保存页面或文件。依赖 Python 3.10+、`httpx`、`PyYAML` 和 `yt-dlp`；视频抽帧、混流与测量需要 `ffmpeg`、`ffprobe`（YouTube `1080p+` 另需本机 `node`）。

本技能仅负责受控下载、容器体检与下载后的证据读取，不替代专业整片参考拆解（有需要时交 `verdantflare-reference-learning`）。

## 2. 能力清单与按任务读取

按目标链接所属协议或平台直达对应原子能力入口；跨平台物理不变量、评分公式与 `Gate G1~G4` 门禁见公共专册：

| 原子能力                 | 入口文档                                                                            |
| :----------------------- | :---------------------------------------------------------------------------------- |
| 通用下载与取证路由主干   | [01.generic-http.entrypoint.md](references/01.generic-http.entrypoint.md)           |
| 抖音与字节系无水印原画   | [02.douyin-media.entrypoint.md](references/02.douyin-media.entrypoint.md)           |
| 小红书与快手状态树直解   | [03.xhs-kuaishou.entrypoint.md](references/03.xhs-kuaishou.entrypoint.md)           |
| B站与海外                | [04.bili-weibo-global.entrypoint.md](references/04.bili-weibo-global.entrypoint.md) |
| 下载后证据读取与研究流程 | [research.md](references/research.md)                                               |

分析或研究请求先读 [01.generic-http.entrypoint.md](references/01.generic-http.entrypoint.md) 与 [research.md](references/research.md)；媒体平台下载按 `classify_platform_and_category()` 分类直达 `02 / 03 / 04` 能力入口。

跨能力公共文档：[00.common.rules.md](references/00.common.rules.md)、[00.common.acceptance.md](references/00.common.acceptance.md)（由各能力入口引用）。

## 3. 工具与命令

| 脚本文件                                                           | 职责简称                                   |
| :----------------------------------------------------------------- | :----------------------------------------- |
| [download.py](scripts/download.py)                                 | 通用 HTTP(S) 下载与 `--research` 清单生成  |
| [mk_video_download.py](scripts/mk_video_download.py)               | 口令提链、`C1~C3` 无水印解析与流式落盘     |
| [video_download_evaluator.py](scripts/video_download_evaluator.py) | `ffprobe` 体检、`Gate G1~G4` 与 100 分评测 |

```text
python -X utf8 scripts/download.py --input "<url_or_share_text>" --output-dir "./.tmp/download-research" --research
python -X utf8 scripts/mk_video_download.py --input "<url_or_share_text>" --output-dir "./.gdrive/downloads" --evaluate
```
