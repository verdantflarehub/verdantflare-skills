# Changes

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
- 仅修改外部指南 `README.md` 或 `Changes.md` 时不升级 Skill 版本。
