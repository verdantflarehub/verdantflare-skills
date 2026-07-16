# VerdantFlare Skills

## verdantflare-video

This repository contains the `verdantflare-video` Skill for Codex. It creates SD2 video tasks through the VerdantFlare API, uploads local references to the configured temporary bucket, tracks asynchronous status, and downloads completed results.

### 安装命令

在 Codex 中执行以下命令，从固定 Release 安装 `verdantflare-video` Skill：

```text
使用 $skill-installer 从 https://github.com/verdantflarehub/verdantflare-skills/tree/verdantflare-video-v0.1.0/skills/verdantflare-video 安装 Skill。
```

安装完成后，Skill 默认位于以下目录：

```text
${CODEX_HOME:-$HOME/.codex}/skills/verdantflare-video
```

### 配置命令

Skill 安装完成后，在 macOS 终端执行：

```bash
bash "${CODEX_HOME:-$HOME/.codex}/skills/verdantflare-video/scripts/install-config-macos.sh"
```

执行前请确认本机已安装 `python3 >= 3.10`。脚本会通过隐藏的 TTY 提示读取一次性 HTTPS 配置地址：

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

其余配置项均为可选，使用当前 Skill Release 的内置默认值。脚本会自动校验配置、创建权限受限的配置和状态目录，并在需要时下载和校验固定版本的 MinIO `mc`。用户不需要手工安装 `mc`、配置 Alias 或编辑 `.env`。

### 使用 Skill

配置成功后，可在 Codex 中调用 Skill：

```text
使用 $verdantflare-video，根据 ~/Desktop/product.png 生成一个 9:16、10 秒的产品广告视频。
```
