# VerdantFlare Skills

## verdantflare-music

`verdantflare-music` 是从一句灵感推进到最终母带交付的 Codex Skill。它负责编制词曲企划、调用 VerdantFlare Music 制作工具，并通过五个人工审核点管理候选、分轨、个人音色、音色转换和母带结果。

输入是一句音乐灵感，以及原始人声 MP3 或一个已批准的人声模型。原始人声和训练模型按创作者管理并可跨歌曲复用，歌曲交付物统一放在当前工作区的 `.output/music/<创作者>/<项目>/`。

### 安装命令

当前版本：`verdantflare-music-v0.3.1`

在 Codex 中执行：

```text
使用 $skill-installer 从 https://github.com/verdantflarehub/verdantflare-skills/tree/verdantflare-music-v0.3.1/skills/verdantflare-music 安装 Skill。
```

### 使用 Skill

```text
使用 $verdantflare-music，创作一首 3 分 20 秒、黑暗电影感的中文叙事歌曲，并在每个审核点等我确认。
```

Skill 通过 VerdantFlare Station 提供的 Music MCP 工具执行生成、分轨、音色训练与转换、已知歌词强制对齐、混音母带。Music3 候选使用最大生成时长作为上限并保留自然结尾，实际时长在审核点记录。音频、真人录音和人声模型不进入 Git。

最终 MP3 使用 `<创作者显示名>-<歌曲名>.mp3` 命名，例如 `Mengsk-今天请嫁给我吧-测试.mp3`。

## verdantflare-video

`verdantflare-video` 是兼容 macOS 和 Windows 的 Codex Skill，用于通过 VerdantFlare API 生成 SD2 视频。

### 安装命令

当前版本：`verdantflare-video-v0.3.1`

在 Codex 中执行：

```text
使用 $skill-installer 从 https://github.com/verdantflarehub/verdantflare-skills/tree/verdantflare-video-v0.3.1/skills/verdantflare-video 安装 Skill。
```

### 配置命令

macOS：

```bash
bash "$HOME/.codex/skills/verdantflare-video/scripts/install-config-macos.sh"
```

Windows PowerShell：

```powershell
& "$HOME\.codex\skills\verdantflare-video\scripts\install-config-windows.ps1"
```

本机需要 Python `3.10` 或更高版本。配置脚本会从内置的 VerdantFlare 引导地址自动下载配置。

配置必须包含以下三项：

```dotenv
VERDANTFLARE_VIDEO_API_KEY=<required>
VERDANTFLARE_VIDEO_S3_ACCESS_KEY=<required>
VERDANTFLARE_VIDEO_S3_SECRET_KEY=<required>
```

### 使用 Skill

```text
使用 $verdantflare-video，根据 ~/Desktop/product.png 生成一个 9:16、10 秒的产品广告视频。
```

## verdantflare-image

`verdantflare-image` 是专注于原子图像资产生成、以图生图与局部重绘的领域级 Skill。它负责将上层视觉意图（如 MV 人物设计胸部四视图、服装无脸人台三视图、分镜单格图 F01~F08、纯场景设计图）转化为技术受控的生图请求，默认采用 `codex`（`gpt-image-2.5-sunburst`，亦支持 `gpt-image-2.5-flare`）引擎驱动，亦支持 `gemini`（`gemini-3.1-flash-image`），通过 `image.*` MCP 工具与 REST 接口调度执行，最终将技术合格且校验 SHA-256 的不可变 `ImageCandidate` 资产受控归档到指定 `source/` 目录。

### 安装命令

当前版本：`verdantflare-image-v0.1.0`

在 Codex 中执行：

```text
使用 $skill-installer 从 https://github.com/verdantflarehub/verdantflare-skills/tree/dev/skills/verdantflare-image 安装 Skill。
```

同时在本地 Codex 注册 Image MCP 服务：

```bash
codex mcp add verdantflare-image \
  --url "${IMAGE_MCP_URL}" \
  --bearer-token-env-var IMAGE_MCP_BEARER_TOKEN
```

### 使用 Skill

```text
使用 $verdantflare-image，为项目 creator/project-demo 生成 B01 单元的角色服装无脸人台三视图，采用 codex 引擎，画幅 16:9。
```

### 命令行客户端 (CLI)

技能随附纯 Python 标准库驱动工具 `scripts/image_client.py`，支持独立在终端执行生图、轮询与哈希校验下载（默认使用 `codex` 引擎）：

```bash
python3 skills/verdantflare-image/scripts/image_client.py generate \
  --prompt "科技风极简标志设计，绿色与深色背景" \
  --engine codex \
  --output /tmp/test-image.png
```

## 变更记录

详见 [`Changes.md`](Changes.md)。
