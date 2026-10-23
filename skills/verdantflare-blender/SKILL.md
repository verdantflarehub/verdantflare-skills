---
name: verdantflare-blender
description: Inspect and edit a specified VerdantFlare Blender instance through its Studio MCP connection, save scenes to Project and recover known operations. Use for live scene work; not for Blender tutorials, offline code review or application deployment.
---

# VerdantFlare Blender

AI 短剧任务形成阶段成果后默认同步到原 Studio 项目，沿用真实 project_id；用户指定仅本地或只读讨论时遵循其范围。候选、返修与最终结果保留固定版本、修改原因、来源设定和任务记录，按当前 Project / Artifact 契约提交并回读修订；只上传或本地保存不算已同步。响应未知先对账恢复，不重复收费生成。同步不自动批准、选用或发布 World。

## 1. 选择实例

使用用户明确的 Studio 实例连接，例如 `/mcp/blenderA`，从已有连接或受控目录取得完整地址。
它与中央 `/mcp` 分别发现工具；同一个有效 Studio Bearer 可复用，实例权限仍逐个校验。
不得因 A 不可用转到 B，不使用独立下游公网 MCP。

先核对真实 tools/list、绑定 project_id 和任务范围。现有名字包括 session.open、scene.get、object.create、project.save，以及 v0.1.14 的 workspace.clean、script.run_project、scene.inspect_file、scene.open_file、project.export_assets；不套用早期 blender.* 草案名。
这里的 project.save 与 project.export_assets 属于实例入口，不发送到中央 Project 域。

## 2. 场景操作与数字片场批处理

按[会话与恢复](references/sessions-and-recovery.md)打开 read/edit 会话，核对实例、项目、代次与 `lease_ttl_seconds: 1800`（30 分钟租约超时）。
GUI 或其他编辑者占用（`INSTANCE_EDIT_LEASE_HELD`）时报告，不抢占控制权或重启实例。

使用 scene.get 读取基线，只执行用户任务需要的对象操作。变更前固定幂等键、原参数和要求的 scene_version。
操作后读回对象与场景版本；超时先对账，不重复创建对象。

需要执行数字片场中模搭建与多机位预演渲染时，直接使用实例 MCP 扩展工具，无需 `kubectl exec` 直接操作 Pod：

- 使用 `workspace.clean` 预览或清理受控导出子目录（`export_subdirs`）与家目录异常残留白名单（`stray_home_names`，仅限 `"C:"`、`"D:"`；默认不删除任何家目录路径）：先传 `dry_run=true` 预览 `would_clean_paths` 清单，再传 `dry_run=false` 执行。
- 使用 `script.run_project` 从已授权 Studio 项目（`source_project_id`，默认会话项目）拉取受控 `bpy` 脚本（如 `blender/scripts/saw_set_v1.py`），在 `/workspace/exports/<output_subdir>/` 隔离运行批处理建模与多机位预演渲染（单实例最大并发 1，单任务超时 `timeout_seconds` 默认 300s／上限 600s，导出目录受文件数与容量配额约束），输出 `.blend`、`.mp4`、`.json` 与 `build.log`，可按需指定 `load_blend_as_active` 将生成的 `.blend` 加载为当前活动场景；返回 `running` 时通过 `operation.get` 轮询直至 `completed`。注意：批处理完成后编辑租约保持有效，不会自动释放。
- 使用 `scene.inspect_file` 对当前活动场景或指定工作区 `.blend`（`asset_id`，如 `exports/<subdir>/xxx.blend`）执行深度结构核验，读取单位制、分辨率、帧率、帧范围、前缀分类计数、全部摄像机参数（焦距、传感器宽度、位置、旋转、关键帧列表）、标签与关键对象世界包围盒尺寸。
- 使用 `scene.open_file` 在保留当前活动场景检查点的前提下，将工作区受控 `.blend` 加载为当前活动场景并递增 `scene_version`。
- 需要导出供 Studio 网页片场查看器（`viewer`）使用的 **Set Package v1.0**（`set.json` + `scene.glb` + `poster.png` + `cameras/CAM_*.png`）时，经 `script.run_project` 运行通用导出器 `scripts/set-package/blender_export_set_package.py`，再通过 `project.export_assets` 归档至目标项目 `sets/<package_id>/` 目录，回读后执行 `python scripts/set-package/validate_set_package.py <pkg_dir>` 与 `python scripts/set-package/check_camera_alignment.py` 完成契约与机位对齐核验（详见[工程归档](references/project-save.md#5-三维片场包-set-package-v10-导出与校验)）。

## 3. 保存、跨项目归档与验证

scene.save 只保存实例工作区。用户要求保存到绑定项目时，按[工程归档](references/project-save.md)调用实例 project.save；在数字世界共用实例（如三国志实例）中向分篇 Studio 项目批量归档中模 `.blend`、多机位预演 `.mp4` 与元数据 `.json` 时，调用 `project.export_assets`，指定 `target_project_id`、`batch_operation_id`（关联批处理操作 ID 以记录来源溯源）及可选 `provenance_path`（服务端实时校验调用者对该目标项目的写权限，不改变实例自身的 `project_id` 绑定）。
等待 operation.get 明确完成，再从中央 Project/Artifact 回读固定修订、导出文件内容与 `provenance` 溯源记录。
应用已执行上传和提交，客户端不重复保存同一检查点。

哈希正确只证明字节完整。需要证明可继续编辑时，必须实际加载并核对场景；对预演 `.mp4` 视频须完整解码核验帧数／时长并逐机位抽帧检查画面主体可见、未被替身或天花板遮挡。首次恢复使用服务提供的受控流程，不能删除引用或改空项目绕过。

## 4. 收尾与释放租约

单次任务或“跑脚本 → 核验 → 归档”闭环结束时，**必须调用 `session.close` 显式释放本次取得的 `editing_session_id` 编辑租约**（租约默认保持 30 分钟且不会随批处理自动释放）；仅长期交互按用户要求保留。释放失败记录恢复入口，不关闭其他人的会话。
返回实例、操作与保存状态、Project 修订、内容校验、来源溯源及已知缺口；技术成功不代替创作或视觉验收。
