# Artifact 传输与恢复

## 1. 工具与传输分工

优先用宿主 MCP 发起管理操作；没有宿主工具时，可使用 Studio 0.5.50 的 `studio-workspace tools` 和 `call --input request.json`。
请求形如 `{"name":"artifact.read","arguments":{...}}`，省略号仅说明结构，真实参数由 tools/list 和实际 ContentRef 填入。
客户端读取进程的 STUDIO_MCP_URL / STUDIO_MCP_BEARER_TOKEN；无需登录。缺客户端时说明依赖，不改用下游公网地址。

二进制字节通过 HTTP 流传输，不嵌入 MCP JSON 或 base64。管理工具成功、上传完成、版本提交和项目归档分别核验。
MCP 响应检查 error、isError、structuredContent 和业务状态，不只检查 HTTP 状态码。

## 2. 准备与上传

先将原请求和来源文件的 size、SHA-256、MIME 保存在明确的恢复记录中，凭据除外。
write_id 为固定的小写 UUIDv7，外部 source 为 `{kind: user_import或user_edit, project_id: 真实ID}`。

调用 artifact.write mode=prepare，参数带 write_id、source、mime、size、sha256。
替换既有内容身份时仅在任务明确且有权时带 artifact_id；不覆盖旧 version_id。
保存返回的 upload_id 和 upload.content_path 后再传字节。

只允许 `PUT /v2/artifacts/uploads/{upload_id}/content`，upload_id 必须与响应一致。
以 Studio origin 解析相对路径，不能把 `/mcp` 与路径直接拼接；拒绝 `//`、绝对 URL、路径穿越、意外查询和重定向。
网络库禁用自动跳转，凭据只从环境加入当前 Studio 请求头。大文件使用流和明确 Content-Length，不整文件塞入命令行或日志。

PUT 后调用 artifact.write mode=commit，输入 upload_id。提交结果中的 version 才是不可变内容。
核对 size、sha256、mime，并提取 store_id、artifact_id、version_id 为 ContentRef。
store_id 是存储实例 ID，不是 URL 或连接别名。

## 3. 响应丢失

- prepare 响应丢失：复用原 write_id 和完全相同的声明。
- PUT 响应丢失：用 mode=status 查询原 upload_id；prepared 不证明完整，可按同一路径完整重传原字节。
- commit 响应丢失：查询原上传状态，重试同一 upload_id 的 commit 取得原版本。

原文件变化后不继续原声明。先保留旧操作并查明状态，新的内容另建请求。
已有 workspace journal 的任务用 workspace resume，不并行维护第二套恢复状态。
持续不可用时保留恢复入口并报告，不无限重试，也不删除旧上传掩盖失败。

## 4. 受控下载

artifact.read mode=download 输入固定 content_ref。
若采用项目授权路径，access 同时包含 project_id、project_revision_id；资产路径同时包含 asset_id、asset_version_id，不混搭。

结果给出 version 与 content_path。只接受同一 Studio 的 `/v2/artifacts/{version_id}/content`，查询中的 store_id、artifact_id 必须对应 ContentRef。
额外查询仅可为服务返回的完整项目修订或资产版本访问路径，不添加身份头或任意上游地址。

下载到新临时文件，按流计算 SHA-256 和总字节数。禁止重定向及跨域凭据转发；拒绝超出任务/服务上限的响应。
核验与 version 一致后，以不覆盖已有目标的方式保存；存在不同内容时保留两份或报告冲突。

当前不支持 Range。传输默认上限 1 GiB，实际取客户端、Studio、Artifact 和资源条件共同约束；不擅自放宽。
小文本读取限合法 UTF-8 和 1 MiB 字节，超过时走下载。

## 5. 来源与归档

Image/Video 等原生结果须经对应服务适配为中央内容；或在用户明确导入时以 user_import 保存，保留原任务记录。
不伪造 task_output/original_ref，也不把中央 version_id 传给要求原生 source_artifact_id 的工具。

上传只得到内容版本。将该版本加入 Project 要另做显式提交并回读修订，权限撤销或未就绪时准确报告，不能自动公开文件。
