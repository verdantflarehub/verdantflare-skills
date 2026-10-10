# Project / World 实际调用入口

四个制作 Skill 共用 Studio 的 `studio-workspace` 客户端；不在 Skill 中重写工作副本、
上传 journal 或工程清单生成逻辑。客户端从 Studio release CI 下载与系统匹配的
`studio-v0.5.50-workspace-windows-amd64` 或 `studio-v0.5.50-workspace-linux-amd64`
产物，也可在 Studio 源码中执行 `bash scripts/build.sh workspace`。

## 会话与作用域

宿主通过环境提供 `STUDIO_MCP_URL`（Studio 根地址或 `/mcp`）和
`STUDIO_MCP_BEARER_TOKEN`（代表用户身份，由服务端解析真实 Core 主体）。只接受 HTTPS 或本机
loopback HTTP。客户端无需额外登录；认证失败时检查服务端令牌归属、有效期及撤销状态，
不自报用户/组织，不索取账号密码、S3 或内部服务凭据。token 不进入命令参数、请求 JSON、
`.vf` 或日志。输入输出均为 UTF-8 JSON，Windows 子进程读取须明确 UTF-8。

直接可用的 Studio MCP 工具仍优先使用；CLI 是需要本地文件读写或外部进程调用时的入口。

认证排查见 [环境说明](../ENVIRONMENT.md)。媒体工具可见而 `project.*` / `artifact.*` 不可见时，
检查服务端Token归属、旧网关过滤逻辑和服务注册，再用同一Bearer重新发现工具。不要通过
额外登录绕过问题，不要伪造项目或主体头，也不要把“客户端未适配”报告成“服务不可用”。

当前 `studio-workspace` CLI 只需上述Bearer，不直接读取浏览器Cookie，也不要求登录。
`v0.5.50` 已发布 `studio-workspace tools` 只读发现入口；旧 `v0.5.36`
产物尚无此子命令，可通过 `--help` 确认。`call` 与工作副本操作沿用同一凭据。

## 显式工作副本操作

每次显式传入已打开的服务 `project_id`、本地目录和连接别名。目录必须已存在；
命令输出包含服务生成的规范化清单，不能自行改写 `.vf/project.json`。

```text
studio-workspace open --dir <directory> --alias <connection> --project-id <project-id>
studio-workspace status --dir <directory> --alias <connection> --project-id <project-id>
studio-workspace fetch --dir <directory> --alias <connection> --project-id <project-id> --file-id <file-id>
studio-workspace save-texts --dir <directory> --alias <connection> --project-id <project-id> --input text-files.json
studio-workspace save-files --dir <directory> --alias <connection> --project-id <project-id> --input media-files.json
studio-workspace import --dir <directory> --alias <connection> --project-id <project-id> --input imports.json
studio-workspace resume --dir <directory> --alias <connection> --project-id <project-id>
```

`text-files.json` 是明确选定的既有 `file_id` 数组。`media-files.json` 是对象数组，每项
为 `path`（工作副本相对路径）、`role`、`mime`，替换既有媒体时附其 `file_id`。
`imports.json` 同样为数组，每项为外部文件绝对 `source_path`、目标相对 `path`、
`role`、`mime`；目标已存在时拒绝覆盖。所有 JSON 都是操作输入，不是另一份工程数据库。

文本保存、新文件导入及媒体保存复用同一 Project/Artifact 链路。二进制按流传输，
校验大小及 SHA-256；默认 fetch 上限为 1 GiB，可显式指定 `--max-bytes`，实际能力仍
受服务上限和磁盘空间约束。保存中断或响应未知时使用 `resume`，保留原 write_id 与
commit_id；不要删除 journal 或重新生成一次任务来掩盖失败。

`switch-head` 仅在本地无改动、无待恢复提交时切换基础修订，不覆盖文件；切换后仍按需
fetch。并发提交冲突保留本地正文与 pending 请求，不自动合并或强制提交。

## 项目与固定资产操作

`studio-workspace call --input request.json` 调用中央 Project/World/Artifact 管理工具。
请求文件为 `{"name":"project.list","arguments":{}}` 这样的 MCP tool 参数；具体字段
遵循中央契约。该入口不开放生成工具，不能意外重复付费任务。

创建/提交/World 登记前先把含固定 `commit_id` 的请求保存到本地，再发送；响应未知时
复用原文件或查询对应 `project.commit_status` / `world.commit_status`。
`project.create` 返回真正 Project ID；`world.get` 必须带固定资产版本；登记前明确
选定来源修订和文件集合；`project.use_asset` 明确目标项目基础修订及资产版本。
CLI 不从文件名、下载成功或先前使用推断已批准入库。

已有制作结果可作为显式用户导入保存，仍保留原任务 ID 和原始文件。CLI 不伪造
`task_output` 或 `original_ref`，可信原生产物映射必须由相应服务适配器完成。
只有原生Artifact结果时，保存到中央Artifact并提交Project后才具备跨电脑恢复的文件引用；`image.result` 或 `video.result` 成功本身不证明这一步已完成。中央 `ContentRef` 也不能直接当作生成服务的原生 `source_artifact_id`。
导入旧 YAML/音乐/MV 清单时仍按各 Skill 的兼容规则转换为领域 JSON；本入口不扫描
目录，也不自动替用户选择素材或升级资产版本。
