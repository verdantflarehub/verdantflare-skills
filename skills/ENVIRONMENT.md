# Skill 配置入口

仅在连接、运行客户端或排查配置问题时读取。`.env.example` 是变量说明，不参与运行；不要输出密钥，也不要用 shell `source` 执行配置文件。

| 执行入口 | 当前实际加载规则 |
| --- | --- |
| Image `scripts/image_client.py` | 进程中的 `IMAGE_MCP_URL` / `IMAGE_MCP_BEARER_TOKEN` 优先；缺项从 `IMAGE_MCP_ENV_FILE` 指向的现存文件补齐，否则从当前目录逐级向上找到的第一个 `.env` 补齐。若要使用技能目录配置，从该目录运行或显式指定路径；无效显式路径当前会回退搜索。 |
| SD2 `scripts/video_client.py` → `config.py` | `VERDANTFLARE_VIDEO_ENV_FILE` 指定配置路径，否则搜索平台配置目录及 WSL 的 Windows 配置目录；多文件时要求指定一个。配置值只读选中的文件，不使用进程同名密钥覆盖，不读取技能/项目 `.env`。 |
| 显式调用 `scripts/load_env.py` 的宿主集成 | 合并进程环境 > 技能目录 `.env` > 当前目录向上首个 `.env`。该 helper 不会自动被所有客户端调用；单独运行子进程不会修改父进程环境。 |
| 宿主提供的 Music / MV / H3 MCP 工具 | 使用宿主已经配置的连接与鉴权；不得假定本地 helper 配置了远端 MCP。 |

SD2 平台路径：Windows 为 `%LOCALAPPDATA%/VerdantFlare/Video/.env`；macOS/Linux 为 `~/.config/verdantflare/video/.env`。安装恢复步骤见 [SD2 工作流](verdantflare-video/references/sd2-workflow.md)。单独安装 Skill 时，若共享说明不在包中，以上述客户端的实际加载器和 `--help` 为准；不要为补配置自动切换服务或输出配置正文。
