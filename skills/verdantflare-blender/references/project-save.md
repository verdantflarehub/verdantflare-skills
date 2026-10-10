# Blender 工程归档

## 1. 两种保存

scene.save 写实例持久工作区，仅证明实例内保存。
project.save 才生成不可变 `.blend` 检查点，经 Artifact 写入并提交 Project 修订。
此流程由 Blender 应用服务执行，客户端不再另做一次上传或 Project commit。

当前归档单位是单个 `.blend`。贴图等用户资源须由制作者在 Blender 中打包；
未打包的贴图、链接库等外部依赖，即使当前路径可读，也不能作为可移植工程归档。
不要自动导入或打包用户任意路径，先确认所需资源和交付范围。

## 2. 发起与查询

在实例 MCP 调用 project.save，参数为 editing_session_id、scene_version、idempotency_key，实际要求先核对工具 Schema。
scene_version 来自本次场景读回或已完成操作，不使用固定常量或猜测值。

保存原请求后发送，记录返回 operation_id；project.save 初始返回 running 不代表归档完成。
在同一实例调用 operation.get，等待明确 completed，并读取其 result 中的实际保存结果。
轮询间隔与总时限按任务确定，超时保留 operation_id 供恢复，不另起保存。

保存使用会话工作副本基线，不因远端 head 前进就把旧场景强制基于新 head 提交。
冲突保留检查点、原 operation_id 和提交信息；明确处理差异后再继续，不覆盖别人的修订。

## 3. 中央回读

使用普通 Studio `/mcp` 的 project.open 打开保存结果中的固定 revision_id。
在返回 manifest.files 中找到本次 `.blend` 文件及 content_ref，不根据文件名构造版本 ID。

调用 artifact.read mode=metadata/download，提供固定 ContentRef，以及完整 project_id/project_revision_id 访问路径。
下载仅走同一 Studio 返回的受控相对路径，拒绝重定向及跨域凭据转发。
临时文件完整下载后核对 size、SHA-256，再保存到明确且不覆盖原文件的目标。

没有宿主中央工具时，可用 Studio 0.5.50 的 studio-workspace call/fetch。
call 参数为 `{name, arguments}`；客户端环境仍为 STUDIO_MCP_URL 和 STUDIO_MCP_BEARER_TOKEN，不能把实例 URL 填入中央管理连接。
缺少该客户端或中央权限时，保留已取得的保存结果，明确回读未验证。

## 4. 恢复与验收

上传或提交响应丢失继续查询原 operation_id，应用复用原检查点、write_id 和 commit_id。
返回 unknown 时先对账，不把新建检查点作为“恢复”。

PROJECT_EXTERNAL_DEPENDENCIES 表示保存检查点或恢复前明确拒绝，当前工程尚不满足单文件交付要求。
记录原操作失败结果；资源补齐并经用户确认打包后，读取新场景版本，以新幂等键提交修改后的保存请求。
PROJECT_FILE_INVALID 表示恢复预检无法读取工程；保留原文件并核对来源，不把它当作已恢复。

交付记录实例、固定修订、ContentRef、字节与摘要核验结果。
可继续编辑必须另做实际打开及对象、变换、外部资产依赖检查；仅 `.blend` 后缀或哈希不能证明工程可恢复。
