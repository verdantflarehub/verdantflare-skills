# Project 修订与恢复

## 1. 实际调用

通过宿主中央 Studio MCP，或工作副本客户端的 `call`：

```text
studio-workspace call --input request.json
```

请求是 `{"name":"project.list","arguments":{}}` 这样的 MCP 工具调用。
参数按当前 tools/list，不把 JSON-RPC id、request_id 或自报组织额外塞入拒绝未知字段的业务 Schema。
读取 `structuredContent` 或工具规定的结果，检查 JSON-RPC error、isError 和业务状态，HTTP 200 不是成功判据。

## 2. 请求与身份

创建需要 commit_id、name、category、entry_path、entry_text。
入口为 MD/TXT，项目及修订 ID 由服务分配。需要客户端生成的 commit_id/write_id 使用小写 UUIDv7，并在发送前固定。

提交使用 project_id、expected_revision_id、commit_id，以及 changes 或 manifest，二者不同时传。
优先最小 changes；字段缺失表示不修改，空集合可能表示清空，不能作为默认填充。

逻辑路径是相对路径，使用 `/`，不得含 `..`、绝对路径或保留的 `.vf`。
新增文件由服务分配 file_id；改路径保留已有 file_id。替换内容后的选用失效需要显式核对。

## 3. 内容与固定资产

文本 upsert 使用真实 path、role、mime、text；二进制引用使用服务返回的完整 ContentRef。
MD 与关联 JSON、选用在同一次提交保持一致；原生任务保留原 service_id/run_id，不为符合平台 UUID 改写它们。

`project.use_asset` 需要明确 asset_id、asset_version_id、purpose、项目基线和 commit_id。
它引用固定版本，不授予来源项目权限，也不执行 World 发布或默认升级版本。

## 4. 结果未知与冲突

`project.commit_status` 使用原 project_id 和 commit_id。
preparing 仍在准备；committed 回读原结果；conflict/failed 保留原草稿和错误，不伪装成功。
新建响应完全丢失时，project_id 尚未知，重发原 project.create 请求找回结果。

同 ID 同请求才能幂等；相同 ID 换输入会发生 IDEMPOTENCY_CONFLICT。
修订冲突读取 current head，明确合并后新建 commit_id，不修改旧请求并复用旧 ID。
网络暂不可用保留恢复记录，不无限重试；轮询需有任务范围内的时间上限。
