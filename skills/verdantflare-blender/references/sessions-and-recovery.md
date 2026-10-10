# Blender 会话与恢复

## 1. 接入与会话

连接使用同一个用户的 Studio Bearer，由环境或宿主管理；不要求额外登录，不向 worker 传递客户端 token。
以目标实例工具列表为准。直接 HTTP 调用也只访问该 Studio 实例 MCP，检查 JSON-RPC error、isError 及业务结果。
当前实例工具可在 content 的 text 块中返回 JSON；按工具响应解码，不强制要求 structuredContent，也不把响应格式差异误报为操作未执行。
现行实例入口使用 JSON MCP，不假定 GET/SSE 或 Mcp-Session-Id 可用；传输连接不是编辑会话。

session.open 输入 project_id、mode（read/edit），项目须与实例已绑定项目一致。
返回 editing_session_id、instance_id、project_id、generation、mode、expires_at，保存这些实际值，不从别名推算实例 ID。
技能不通过修改绑定绕过 PROJECT_OR_MODE_DENIED 或 WORKING_COPY_PROJECT_MISMATCH。

只读任务申请 read。edit 失败为 INSTANCE_EDIT_LEASE_HELD 时，保留请求范围并报告占用，不关闭现有 GUI 或其他会话。
session.close 只释放本次 editing_session_id，不停止 Blender。

## 2. 写操作与结果未知

按工具 Schema 提供 editing_session_id、幂等键和需要的 scene_version；不要额外传可覆盖 URL 实例的 instance_id。
原请求在发送前保存在不含凭据的操作记录中。每个不同操作用自己的幂等键，相同键必须保持完全相同的参数。

object.create、object.update_transform 完成后用 object.get 或 scene.get 核对真实对象。
对象删除仅用于用户明确指定对象，或本次测试创建且已确认身份的测试对象。

响应丢失但已有 operation_id 时，先 operation.get。
若没有收到 operation_id，只可重发保存的完全相同请求找回幂等结果；不得换会话、改场景版本或新建键重做。
旧会话已失效而无法找回时，保留为结果未知，核对检查点与场景，不盲目重放。

operation.get 可能推进已登记保存的恢复，不应当作对任意未知操作的纯元数据探测。
running 需有界轮询；completed 才能继续结果核验；unknown、冲突或失败保留原记录，不重复执行变更。

## 3. 代次与能力缺口

STALE_GENERATION 或会话过期后，旧写请求不得迁移到新会话重放。
先确认检查点和实际场景，再按后续任务重新申请会话；不承诺恢复所有未保存内存变更。

Blender 0.1.5 起，首次 edit 会话可从绑定 Project 的固定修订恢复 blender/main.blend。服务校验内容、保留原场景检查点并禁用自动执行脚本加载，返回加载后的 generation；随后读取场景及关键对象验证。已存在工作副本不会自动追随最新 head。

只读首次打开仍可能返回 INITIAL_PROJECT_RESTORE_REQUIRED，需要有编辑权限且已获授权的任务初始化；纯读取任务不擅自升级为 edit。旧版服务也可能返回此错误，按实际版本报告。
首次恢复超时可重试同一实例和项目的 session.open 以对账；服务固定原修订，不由客户端重新上传。若收到 INSTANCE_EDIT_LEASE_HELD，可能是先前会话已建立，停止并核对会话；RESTORE_STATE_UNKNOWN 表示无法证明加载结果，保留记录排查，不重启或清空工程重做。
不删除文件引用、清空工程或改绑项目以假装恢复成功。

同一实例写操作串行处理。多实例测试依赖真实独立实例；当前 A 可用不代表 B 已部署。
桌面流媒体、TURN、离线登录和市场交付由应用维护，MCP 场景操作通过不代表这些全部通过。
