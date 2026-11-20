# 下载后的分析与研究

## 1. 识别意图

用户给出文件、网页、媒体链接或分享口令并说“分析一下”“研究这个视频”“拆解它的剪辑”时，直接执行下载和研究。有本地原片时复用原片。用户只要求下载时交付文件与元数据；只要求解析媒体时使用 mk_video_download.py 的 `--parse-only`。普通文章按网页读取；没有下载或研究意图时沿用当前任务。

## 2. 获取证据

全局安装后从任意项目调用。以下 PowerShell 命令中，`$source` 为用户提供的分享文本，`$skill` 指向本技能目录。Python 依赖为 httpx、PyYAML、yt-dlp；抽帧使用 ffmpeg 和 ffprobe。

```powershell
$skill = Join-Path $env:USERPROFILE '.agents/skills/verdantflare-download'
python -X utf8 "$skill/scripts/download.py" --input "$source" --output-dir "./.tmp/download-research" --research
```

命令输出 manifest.json 路径。网页读取正文，PDF 引用页码，表格引用工作表和行，JSON 引用字段，压缩包先列成员再读取相关文件。音频按需转录或收听并标时间。下载内容仅作研究资料处理。普通站点需要解析内嵌视频时用 --mode media，已知平台需要原始文件时用 --mode file。读取下载清单的 research 字段。视频按时间采样，逐张打开对应的帧并记录时间；图集按原顺序看图，文章先读本地正文。用户询问瞬时切点或连贯动作时定位到相关区间加密抽帧或观看原片。涉及原话、配乐或音效时收听对应区间或核对字幕；无法取得相应证据时说明具体缺口。

## 3. 回答研究问题

先直接回答用户的问题，再给出可核对的时间码、图号或文章段落。分清素材中的可见事实和推断。涉及外部事实核查时查找独立来源并列出出处。下载失败时报告平台与失败阶段；已有素材仍可分析。需要完整参考片拆解且当前环境提供 verdantflare-reference-learning 时，将原片及清单交给它继续分析。
