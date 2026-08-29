# VerdantFlare Skills

## verdantflare-music

`verdantflare-music` 是从一句灵感推进到最终母带交付的 Codex Skill。它负责编制词曲企划、调用 VerdantFlare Music 制作工具，并通过五个人工审核点管理候选、分轨、个人音色、音色转换和母带结果。

输入是一句音乐灵感，以及原始人声 MP3 或一个已批准的人声模型。原始人声和训练模型按创作者管理并可跨歌曲复用，歌曲交付物统一放在当前工作区的 `.output/music/<创作者>/<项目>/`。

### 安装命令

当前版本：`verdantflare-music-v0.3.0`

在 Codex 中执行：

```text
使用 $skill-installer 从 https://github.com/verdantflarehub/verdantflare-skills/tree/verdantflare-music-v0.3.0/skills/verdantflare-music 安装 Skill。
```

### 使用 Skill

```text
使用 $verdantflare-music，创作一首 3 分 20 秒、黑暗电影感的中文叙事歌曲，并在每个审核点等我确认。
```

Skill 通过 VerdantFlare Station 提供的 Music MCP 工具执行生成、分轨、音色训练与转换、已知歌词强制对齐、混音母带。Music3 候选使用最大生成时长作为上限并保留自然结尾，实际时长在审核点记录。音频、真人录音和人声模型不进入 Git。

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

## 变更记录

详见 [`Changes.md`](Changes.md)。
