# VerdantFlare Skills

## verdantflare-video

`verdantflare-video` 是一个兼容 macOS 和 Windows 的 Codex Skill。它通过 VerdantFlare API 创建 SD2 视频任务，上传本地参考素材，跟踪异步任务状态，并下载生成结果。

说明：本 README 是 Skill 的外部安装与配置指南。仅修改本文件时不升级 Skill 版本；只有 `skills/verdantflare-video` 内容发生变化时才发布新版本。

### 安装命令

当前发布版本：`verdantflare-video-v0.2.0`。

在 Codex 中执行以下命令，从固定 Release 安装 `verdantflare-video` Skill：

```text
使用 $skill-installer 从 https://github.com/verdantflarehub/verdantflare-skills/tree/verdantflare-video-v0.2.0/skills/verdantflare-video 安装 Skill。
```

安装完成后，Skill 默认位于以下目录：

```text
macOS:  ${CODEX_HOME:-$HOME/.codex}/skills/verdantflare-video
Windows: %CODEX_HOME%\skills\verdantflare-video (默认：%USERPROFILE%\.codex\skills\verdantflare-video)
```

### 配置命令

Skill 安装完成后，根据操作系统执行对应命令。

macOS 终端：

```bash
bash "${CODEX_HOME:-$HOME/.codex}/skills/verdantflare-video/scripts/install-config-macos.sh"
```

Windows PowerShell：

```powershell
& "$HOME\.codex\skills\verdantflare-video\scripts\install-config-windows.ps1"
```

如果设置了自定义 `CODEX_HOME`，将命令中的 `$HOME\.codex` 替换为该目录。

执行前请确认本机已安装 Python `3.10` 或更高版本。脚本会通过隐藏的 TTY 提示读取一次性 HTTPS 配置地址：

```text
Paste one-time VerdantFlare configuration URL:
```

一次性配置地址由 VerdantFlare 私下提供。

配置地址返回的 `.env` 内容必须包含以下三项：

```dotenv
VERDANTFLARE_VIDEO_API_KEY=<required>
VERDANTFLARE_VIDEO_S3_ACCESS_KEY=<required>
VERDANTFLARE_VIDEO_S3_SECRET_KEY=<required>
```

其余配置项均为可选，使用当前 Skill Release 的内置默认值。脚本会自动校验配置、创建配置和状态目录，并在需要时下载和校验对应平台的固定版本 MinIO `mc`。

### 使用 Skill

配置成功后，可在 Codex 中调用 Skill：

```text
使用 $verdantflare-video，根据 ~/Desktop/product.png 生成一个 9:16、10 秒的产品广告视频。
```
