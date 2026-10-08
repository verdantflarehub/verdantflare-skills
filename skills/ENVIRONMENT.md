# Skill配置与连接排查

只在连接、运行客户端或排查配置时读取。配置从受控环境加载；不输出密钥，不用shell执行 `.env`，不把令牌写进命令参数、任务请求或日志。

## Studio入口与身份

所有业务MCP的外部入口是Studio。`STUDIO_MCP_URL` 指向当前环境的 `/mcp`，`STUDIO_MCP_BEARER_TOKEN` 应绑定真实Core主体；Project/Artifact管理不能使用旧共享媒体令牌。宿主也可使用同源登录Cookie会话，按其认证契约携带Origin；Cookie值不是Core Bearer令牌。

遇到403、管理工具不可见或旧CLI检查失败时：

1. 区分认证失败、工具未注册、渠道未连接、引用未适配和任务执行失败；媒体工具可见不等于Project权限正常。
2. 检查当前令牌类型。已有授权登录信息时恢复真实Studio会话并重新执行 `tools/list`、必要的只读项目查询，不重复索取已配置凭据。工作区测试账号按根AGENTS从受控本地配置读取。
3. 核对当前注册的工具名和参数。现有网关创建名为 `image.create` / `video.create`，不能因旧脚本只认 `.generate` 就报告服务不可用。
4. 分别核对宿主渠道配置、就绪状态和输入规格。工具列表的默认值不证明渠道已接入；静态Schema与匹配部署版本的能力说明冲突时，先核实差异，不试填参数或用收费生成探测能力。

仅有真实Cookie会话时，可用支持该会话的宿主工具。`studio-workspace` CLI当前需要Core Bearer；由宿主提供受控会话适配，不伪造身份头，不把旧共享令牌或Cookie当Core令牌。

## 附带客户端的实际状态

| 入口 | 配置读取 | 兼容边界 |
| --- | --- | --- |
| 宿主MCP工具 | 宿主连接与鉴权 | 首选；使用实际发现的工具和当前Project上下文 |
| `studio-workspace` | 进程 `STUDIO_MCP_URL` / `STUDIO_MCP_BEARER_TOKEN` | 只接受HTTPS或本机loopback HTTP；负责中央Project/Artifact工作副本、上传与恢复 |
| Image `scripts/image_client.py` | 优先Studio变量，兼容Image变量；缺项从 `IMAGE_MCP_ENV_FILE` 或向上首个 `.env` 补齐 | 仍拼接REST `/api/tasks`，未适配Studio `/mcp`；读取Studio变量不代表可直接连接网关 |
| Video `scripts/video_client.py` 的MCP分支 | 优先Studio变量，兼容Video变量 | 新H3生成发现 `video.create`，按 `video.capabilities` 解析渠道；`video.import` 接入授权图片；旧宿主须显式渠道，缺少导入工具不代表生成工具不可用 |
| Video旧SD2分支 | `VERDANTFLARE_VIDEO_ENV_FILE` 或平台历史配置 | 旧公共API/S3客户端，不作为新Studio任务的默认入口 |
| 显式调用 `scripts/load_env.py` 的集成 | 进程 > 技能目录 `.env` > 当前目录向上首个 `.env` | helper不是所有CLI的自动加载器，子进程不会修改父进程环境 |

兼容变量 `IMAGE_MCP_URL` / `VIDEO_MCP_URL` 不授权下游独立公网入口。下载按宿主返回的受控路径解析；不能将 `/mcp` 与下载路径直接拼接，也不向跨域URL转发凭据。

## 历史SD2配置

旧安装使用 `VERDANTFLARE_VIDEO_API_KEY`、`VERDANTFLARE_VIDEO_S3_ACCESS_KEY`、`VERDANTFLARE_VIDEO_S3_SECRET_KEY`，平台文件位于 `%LOCALAPPDATA%/VerdantFlare/Video/.env` 或 `~/.config/verdantflare/video/.env`。这些不是Studio MCP的必填变量。

旧安装器会下载API/S3配置及 `mc`，不是Studio登录修复工具。新任务不自动执行这些安装器，不让Skill持有S3或供应商凭据。现存任务按原模型、渠道、任务ID恢复，详见 [SD2说明](verdantflare-video/references/sd2-workflow.md)。

文件保存与跨电脑恢复见 [统一客户端入口](_shared/project-world-client.md)。独立安装包缺少共享资源时，应补齐同版本依赖或使用宿主提供的对应能力，不臆造配置、工具名或项目ID。
