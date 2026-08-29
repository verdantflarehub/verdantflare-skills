# Changes

## verdantflare-music-v0.3.1

- 最终 MP3 改为 `<创作者显示名>-<歌曲名>.mp3`，不再保留 `Final_Song.mp3` 兼容文件。

## verdantflare-music-v0.3.0

- 新增显式 `lyrics.align` 歌词强制对齐阶段，以转换后干声和已批准逐行歌词生成 `Aligned_Lyrics.lrc` Artifact。
- 对齐结果必须保持行数和逐行文字，时间戳严格递增且不超过节目时长。
- 禁止用原曲 LRC、线性缩放或手工伪造时间戳作为对齐 fallback。

## verdantflare-music-v0.2.0

- 将 Music3 时长语义从“精确固定时长”改为“最大生成时长”，保留模型自然结束的候选。
- 审核点 2 分别记录两个候选的实际时长，不再因为候选未填满上限而失败或反复抽取 seed。
- 用户要求精确节目时长时明确报告当前生成链路不保证该约束，不用裁切或静音伪造结果。

## verdantflare-music-v0.1.0

- 新增从一句音乐灵感到最终母带交付的 `verdantflare-music` Skill。
- 固化双候选生成、UVR5 分轨、RVC 音色训练或复用、音色转换、歌词对齐和混音母带流程。
- 增加五个人工审核门禁、精确时长约束、标准交付物和可恢复审核记录规范。
- 输入收敛为一句音乐灵感，以及原始人声 MP3 或已批准的人声模型。
- 原始人声和模型按创作者统一管理，歌曲交付物按 `<创作者>/<项目>` 管理并引用可复用模型。
- 统一使用当前工作区的 `.output/music/` 作为人声资产和歌曲项目的输出根目录。
- Skill 只负责制作计划与工具选择，不包含 Kubernetes 运维、音频算法或 Station 任务资产管理。

## verdantflare-video-v0.3.1

- 配置安装脚本改为从内置 VerdantFlare 引导地址自动下载配置，无需再通过终端手工输入一次性配置地址。
- `SKILL.md` 已完整翻译为中文，并保留命令、参数、API 字段和状态值原样。
- 增加默认引导地址的安装回归测试。

## verdantflare-video-v0.3.0

- Linux runtime 会优先读取 Linux 配置，并在 WSL 中搜索已挂载的 Windows 配置：`/mnt/<drive>/Users/<user>/AppData/Local/VerdantFlare/Video/.env`。
- 找不到配置时会输出实际搜索路径；发现多个配置时会提示冲突路径。
- 本地素材上传失败时会明确说明 bucket 不存在、未 provision 或无权限。
- bucket 错误发生在视频 `POST` 之前，不代表图片或 API Key 有问题；纯文本请求不经过素材上传。

## verdantflare-video-v0.2.0

- 增加 macOS 和 Windows 支持。
- 增加 Windows PowerShell 配置入口和 Windows `mc.exe` 运行依赖。
- 增加 Windows 配置目录和任务锁兼容处理。

## 发布规则

- `skills/verdantflare-video` 内容变化时发布新的 Skill 版本和不可变 Tag。
- `skills/verdantflare-music` 内容变化时发布新的 Skill 版本和不可变 Tag。
- 仅修改外部指南 `README.md` 或 `Changes.md` 时不升级 Skill 版本。
