# Blender 工程归档

## 1. 两种保存与跨项目批量归档

scene.save 写实例持久工作区，仅证明实例内保存。
project.save 生成当前活动场景的不可变 `.blend` 检查点，经 Artifact 写入并提交实例绑定 Project 的修订。
project.export_assets（v0.1.14 含来源溯源）支持将实例工作区 `exports/` 或 `project/` 下的多个导出文件（`.blend` 中模、`.mp4` 机位预演、`.json` 场景元数据、`.glb` 等）一次性上传至 Artifact，并在 `target_project_id` 指定的 Studio 项目（默认会话绑定项目，支持跨项目指定调用者拥有写权限的分篇项目）上原子提交单个新修订，同时记录来源实例、源脚本项目／修订与批处理 `batch_operation_id`，不改变实例本身的 `project_id` 绑定。
上述流程均由 Blender 应用服务执行，客户端不再另做一次上传或 Project commit。

当前归档单位是单个 `.blend`。贴图等用户资源须由制作者在 Blender 中打包；
未打包的贴图、链接库等外部依赖，即使当前路径可读，也不能作为可移植工程归档。
只处理本工程明确引用且属于已有授权范围的资源，不扫描或打包任意用户目录。
资源路径或交付范围不明时先澄清；已有明确授权不重复请求确认。

## 2. 发起与查询

在实例 MCP 调用 project.save，参数为 editing_session_id、scene_version、idempotency_key，实际要求先核对工具 Schema。
scene_version 来自本次场景读回或已完成操作，不使用固定常量或猜测值。

保存原请求后发送，记录返回 operation_id；project.save 初始返回 running 不代表归档完成。
在同一实例调用 operation.get，等待明确 completed，并读取其 result 中的实际保存结果。
轮询间隔与总时限按任务确定，超时保留 operation_id 供恢复，不另起保存。

保存使用会话工作副本基线，不因远端 head 前进就把旧场景强制基于新 head 提交。
冲突保留检查点、原 operation_id 和提交信息；明确处理差异后再继续，不覆盖别人的修订。

跨项目或多文件归档时，在实例 MCP 调用 `project.export_assets`，传入 `editing_session_id`、`idempotency_key`、`target_project_id`（可选，跨项目归档必填）、`batch_operation_id`（可选，关联前序 `script.run_project` 操作 ID 以生成完整来源溯源）、`provenance_path`（可选，如 `blender/provenance.json`，将溯源清单同步写入目标 Project）、`files`（`[{asset_id, project_path, role}]` 列表）及 `commit_message`。同样通过 `operation.get` 轮询直至 `completed`，从 `result` 读取 `target_project_id`、`revision_id`、`provenance` 与每个文件的固定 `content_ref`、`size`、`sha256`。归档完成后如无后续写操作，立即调用 `session.close` 释放编辑租约。

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
记录原操作失败结果；通过实际可用的 Blender 能力在授权范围内补齐并打包资源后，读取新场景版本，以新幂等键提交修改后的保存请求。
实例工具未提供打包能力时，不臆造工具或任意 Python 入口，明确需要制作者在 Blender 中完成的步骤。
PROJECT_FILE_INVALID 表示恢复预检无法读取工程；保留原文件并核对来源，不把它当作已恢复。

交付记录实例、固定修订、ContentRef、字节与摘要核验结果。
可继续编辑必须另做实际打开及对象、变换、外部资产依赖检查；仅 `.blend` 后缀或哈希不能证明工程可恢复。

## 5. 三维片场包（Set Package v1.0）导出与校验

当任务需要将 `.blend` 片场导出为 Studio 网页片场查看器（`viewer`）可直接加载的 **Set Package v1.0** 时，按以下步骤执行：

1. **运行无头导出器**：确保目标 `.blend` 已通过 `scene.inspect_file` 核验，随后通过 `script.run_project` 在实例 `/workspace/exports/<subdir>/` 运行 `scripts/set-package/blender_export_set_package.py`，产出 `scene.glb`（+Y Up，1.0m，静态同材质网格合并后节点数 $\le 180$）、`set.json`（Schema `1.0`）、`poster.png` 与 `cameras/CAM_*.png`。
2. **归档固定修订**：调用 `project.export_assets` 将上述文件写入目标项目 `sets/<package_id>/` 路径下（其中 `set.json` 的 `role` 设为 `set_package` 或 `metadata`，`scene.glb` 的 `role` 设为 `model`），记录返回的固定 `revision_id` 并在 `finally` 中调用 `session.close`。
3. **执行包校验器与机位对齐检查**：从中央 Project/Artifact 回读下载至本地目录后，运行：
   - `python scripts/set-package/validate_set_package.py <package_dir>`（验证无 `.blend`/`.py` 源文件混入、文件数 $\le 32$、`scene.glb` $\le 25\text{ MiB}$、节点数 $\le 180$、`fov_y_deg` 换算误差 $\le 0.05^\circ$、SHA-256 100% 匹配）；
   - `python scripts/set-package/check_camera_alignment.py --package-dir <package_dir> --output <alignment.json>`（验证网页 Y-up 相机与 Blender 渲染参考图对齐）。
