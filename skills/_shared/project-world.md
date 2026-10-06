# Project / World 接入规则

四个制作 Skill 都运行在当前 Studio Project 上下文中。Skill 不创建第二套项目数据库，也不从本地目录、文件名或 Prompt 猜项目身份。

执行本地文件保存、按需下载、跨电脑打开或外部 MCP 调用时，读取[统一客户端入口](project-world-client.md)。
使用 Studio 的 `studio-workspace`，复用服务生成清单和已有恢复日志；四个 Skill 不另写上传恢复程序。

## 共同规则

1. 新任务使用 Studio 提供的 `project_id`。新建项目时由 `project.create` 生成小写 UUIDv7；Skill 只传递已打开的 ID，不自行生成、拼接或使用 `default`、`creator/name` 这类别名。
2. 生成、导入、训练和转换的结果先归属于来源 Project。工具返回的任务 ID、`ContentRef`、媒体元数据和错误状态写入项目的 MD 审核记录；文件归属、选用关系和任务引用由服务维护的 `.vf/project.json` 投影登记。
3. 本地目录是工作副本。`.vf/project.json` 和 MD/媒体都通过 Project 提交保存到服务端；`.vf/local.json` 只记录本机打开状态。Skill 不实现目录扫描、双向同步或跨电脑复制。
4. 需要复用的结果必须由用户或上层流程显式执行 `world.register`。来源 Project、来源修订和原始 Artifact 保留；World 资产只包含明确选定的固定文件。注册不会自动把整个项目、未选候选、训练录音或私有审核资料公开出来。
5. 使用 World 资产前先以 `asset_id + asset_version_id` 调用 `world.get`，检查授权并解析该固定版本的文件，再把具体 `ContentRef` 作为本次任务输入。不得使用 `latest`、展示名、临时下载 URL 或未解析的 World ID 直接提交。
6. 资产升级是一次新的明确提交：在 Project 中写入新的 `asset_refs` 或选择项，旧修订继续指向旧版本。生成结果不会自动修改 World，也不会静默替换已有项目输入。
7. JSON 只承载工程结构和领域机器字段；企划、Prompt、制作说明、修改理由和人工审核继续写 MD/TXT。新结构只生成 JSON；旧 YAML、`assets.json`、`model.json`、`recordings.json` 等仅作为兼容导入，转换后不得与服务清单并行维护。

## 恢复与失败

- 换电脑时重新 `project.open` 同一 `project_id`，按清单按需取得入口文档和固定依赖；未下载媒体不阻止读取项目。
- 已有任务沿项目中的 `run_refs` 和原生任务 ID 查询、恢复和下载。响应丢失或等待超时不能触发新的收费生成、训练或导入。
- 结果通过技术校验后仍是候选。人工批准、World 入库和 Project 选用是不同动作，不能由文件名、哈希或工具成功状态推断。

中央字段与操作以工作区的 [Project / World 操作契约](../../../docs/design/contracts/project-world-v1.md) 和 v2 Schema 为准；本文件不复制 Artifact URL、凭据或存储路径。
