# Skill 配置入口

仅在连接、运行客户端或排查配置问题时读取。`.env.example` 是变量说明，不参与运行；不要输出密钥，也不要用 shell `source` 执行配置文件。

| 执行入口 | 当前实际加载规则 |
| --- | --- |
| Studio MCP 统一网关 | 遵循单一出口原则，外部客户端统一使用 `STUDIO_MCP_URL`（默认 `https://studio.dev.verdantflarehub.com/mcp`）与 `STUDIO_MCP_BEARER_TOKEN`。 |
| Image `scripts/image_client.py` | 优先读取进程或 `.env` 中的 `STUDIO_MCP_URL` / `STUDIO_MCP_BEARER_TOKEN`，兼容读取 `IMAGE_MCP_URL` / `IMAGE_MCP_BEARER_TOKEN`；缺项从 `IMAGE_MCP_ENV_FILE` 或向上搜索的第一个 `.env` 补齐。 |
| Video `scripts/video_client.py` | 优先读取 `STUDIO_MCP_URL` / `STUDIO_MCP_BEARER_TOKEN`，兼容读取 `VIDEO_MCP_URL` / `VIDEO_MCP_BEARER_TOKEN`。SD2 模式下支持读取 `VERDANTFLARE_VIDEO_ENV_FILE`。 |
| 显式调用 `scripts/load_env.py` 的宿主集成 | 合并进程环境 > 技能目录 `.env` > 当前目录向上首个 `.env`。该 helper 不会自动被所有客户端调用；单独运行子进程不会修改父进程环境。 |
| 宿主提供的 Music / MV / H3 MCP 工具 | 使用宿主已经配置的连接与鉴权；统一汇聚于 Studio MCP 网关。 |

SD2 平台路径：Windows 为 `%LOCALAPPDATA%/VerdantFlare/Video/.env`；macOS/Linux 为 `~/.config/verdantflare/video/.env`。安装恢复步骤见 [SD2 工作流](verdantflare-video/references/sd2-workflow.md)。单独安装 Skill 时，若共享说明不在包中，以上述客户端的实际加载器和 `--help` 为准；不要为补配置自动切换服务或输出配置正文。
