# VerdantFlare Skills

## verdantflare-video

`verdantflare-video` 是兼容 macOS 和 Windows 的 Codex Skill，用于通过 VerdantFlare API 生成 SD2 视频。

### 安装命令

当前版本：`verdantflare-video-v0.3.0`

在 Codex 中执行：

```text
使用 $skill-installer 从 https://github.com/verdantflarehub/verdantflare-skills/tree/verdantflare-video-v0.3.0/skills/verdantflare-video 安装 Skill。
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

本机需要 Python `3.10` 或更高版本。配置脚本会提示输入一次性配置地址。

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

变更记录见 [`Changes.md`](Changes.md)。
