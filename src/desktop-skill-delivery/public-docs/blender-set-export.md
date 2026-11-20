# 固定工程的片场导出与机位核验

## 1. 输入与运行位置

本流程用于已有 Studio Blender 工程的静态片场交付。普通场景读写和 `.blend` 保存不要求先导出片场包。连接、权限和回读沿用[Studio 连接](common-connection.md)、[工程归档](../../../skills/verdantflare-blender/references/project-save.md)及同发行集合的 Project／Artifact 技能。

实例负责运行 Blender；本地准备与校验脚本使用发行集合的受管 Python 3.10+，图像测量还需 Pillow。工程候选缺运行时或实例能力时对应步骤保持未完成，不要求用户安装 Blender、下载私有源码或改系统 Python。脚本全部位于本技能 `scripts/`，下列命令中的解释器应替换为发行工具实际提供的入口；不猜测系统 `python` 已满足依赖。

先通过 `scene.inspect_file` 确认工作区 `.blend` 可重新加载、SHA-256、米制单位、真实对象／相机名称、当前渲染 Scene 和已打包资源。源项目及固定修订必须经 Project／Artifact 回读与工作副本对账，计划中的 UUID 文字本身不能证明权限或来源。导出计划的源工程修订与后续脚本保存修订是两个不同记录，不互相替代。

通用导出器只接受准备好的静态网格和 PBR 材质，保留所选节点标识，不自动合并对象。程序化材质、材质位移／体积、缺失纹理、外链资源、Geometry Nodes／粒子实例、移轴或不受支持的相机滚转会被拒绝。应在原工程的授权修订内先完成资源准备并重新取证，不删除效果或更换工程来绕过。源工程不被本工具保存或覆盖。

## 2. 固定导出计划与脚本

计划使用 `vf.set-export/v1`；以下字段从真实输入填写，不复制测试身份：

- `source`：`asset_id` 为实例 `project/` 或 `exports/` 下的相对 `.blend` 路径；`sha256` 为实际文件摘要；`project_id`、`revision_id` 和 `project_path` 指向该源工程固定修订。
- `package_id`、`title`：包标识与显示名称；标识必须符合校验器命名约束，并与输出目录相同。
- `objects`：明确选择的网格名称，不自动收集全部 Scene；导出前核对业务需要的完整场景和授权范围。
- `cameras`：每项填写 `id`、`object_name`、`label`；可选 `category` 和 `hide_objects`，隐藏项只能来自所选网格。实际光学参数从工程读取，不能手填假 FOV 代替真实机位。
- `navigation`：真实 `orbit_target_blender_m` 和 `walkable_boxes_blender_m`；可走盒逐项具有 `min`／`max`，由业务核查可通行性，结构校验不证明碰撞准确。
- `lighting_presets`、`default_lighting_preset`、`default_camera_id`：与工程和查看器实际预设对账；元数据不是自动烘焙灯光的证明。
- `poster_resolution`：独立 16:9 封面，默认 `[640, 360]`；机位图使用源工程渲染分辨率，执行第 4 节验收时须事先固定为 1344×768。
- 可选 `object_groups`、`kind`、`lineage`、`cast`：按适用的 Set Package 1.0／1.1 层级填写。1.1 来源段保持可选；显式 `setup` 来源链须含 `project_scene` 与 `setup`，空景不得夹带人物节点。

使用[准备器](../blender_tools/prepare_set_export.py)生成一个全新脚本文件：

```text
python scripts/prepare_set_export.py export-plan.json --output prepared-set-export.py
```

准备器嵌入同版本导出器及校验器原字节，校验快照与 `scripts/source-provenance.json` 摘要；不动态下载代码、不访问任意工程路径、不在本地运行 Blender。保存脚本摘要，按 Project／Artifact 技能将脚本写入已授权项目并取得固定脚本修订。

## 3. 实例执行与成果归档

在指定实例当前有效编辑会话调用 `script.run_project`，参数以实际发现的 Schema 为准。显式提供脚本 `source_project_id`、固定 `revision_id`、`script_path` 和新 `output_subdir`；`clean_output_subdir=false`，不设置 `load_blend_as_active`。输出目录与源工程相互独立，默认不改变活动场景；日志和全部输出计入实例配额。

记录幂等键和 `operation_id`，通过 `operation.get` 查询明确终态。响应未知先查原操作，不换键重跑。导出器在新暂存目录渲染第 1 帧、导出 GLB，完成包校验与源摘要复验后才暴露 `<package_id>/`。普通异常退出时只清理本次新建暂存目录，保留原工程和批处理日志；进程被强杀等无法执行清理的情况，先核对原操作与文件清单，按已有清理授权处理，不删除整个工作卷。

成功包为 `set.json`、`scene.glb`、独立 `poster.png` 和各机位 `cameras/CAM_<id>.png`。不得混入脚本、`.blend`、备份工程或日志。通过 `project.export_assets` 归档到目标项目的 `sets/<package_id>/`，将 `batch_operation_id` 关联原批处理；文件角色使用当前工具支持的 `output`／`doc`，不要把其他契约中的 `model`／`metadata` 当作此工具的入参。归档失败保留已生成的包，不重复渲染；单次闭环结束释放本次编辑租约。

从中央 Project／Artifact 回读该固定修订，下载至全新且与 `package_id` 同名的目录。运行[包校验器](../blender_tools/validate_set_package.py)：

```text
python scripts/validate_set_package.py PACKAGE_DIRECTORY
```

校验包含文件摘要、大小、命名、坐标与光学结构及层级语义。它不证明画面、材质、空间完整性或来源权限，不能替代实际加载与业务检查。

## 4. 原生截图与数值测量

查看器加载同一固定修订，从网页实际截图与 Blender 基准图采集证据。固定第 1 帧、1344×768，覆盖包内全部机位。Agent 应提前选定足以判断空间的锚点，并从两侧实际画面观测其像素位置；不能用一套投影公式复制两份坐标，也不能裁切、移动或缩放截图以追平误差。图像须为不透明 RGB／RGBA PNG。

证据 JSON 使用 `vf.camera-evidence/v1`，顶层填写 `manifest_sha256`、`scene_sha256` 和 `cameras`。每个机位项填写：

- `camera_id`、整数 `frame=1`、`resolution=[1344,768]` 和预选 `anchor_ids`。
- `blender` 与 `web`：各自独立、可回查的 `capture_ref`；`image` 含相对证据文件目录的 `path` 与实际 `sha256`；`anchors` 含同名 `id` 与实际 `[x,y]` 像素。
- 透视两侧各填实际 `fov_y_deg`；正交两侧各填 `ortho_width_m`。Blender 图摘要必须等于包内相机基准图。
- 可选 `structure_mask`：与图同尺寸的 0／255 单通道 PNG，填写 `path`、`sha256`、`rationale` 和 `baseline_manifest_sha256`。仅用于已明确的非几何噪声排除，由独立 Agent 核查覆盖充分性，不允许事后只圈相合区域。

使用[测量器](../blender_tools/check_camera_evidence.py)，输出必须为新文件：

```text
python scripts/check_camera_evidence.py --package-dir PACKAGE_DIRECTORY --evidence camera-evidence.json --output camera-measurement.json
```

数值判据为 FOV 差不超过 0.05°、锚点最大偏移不超过 8 px、3 px 容差下双向 Sobel 边缘重合率至少 0.92；正交宽度独立比较。重复／近重复像素图、空白结构、针孔掩膜、错误摘要、缺机位和非法输入会拒绝。充分性检查要求至少 0.01% 画幅像素的最大通道差达到 2，至少 `max(16, ceil(画幅像素数×0.01%))` 个结构边缘；掩膜至少覆盖 1% 画幅并保留各侧 25% 原始边缘。未达要求保留原证据和原因，不改图制造差异；这些防误用检查不证明来源真实性。

退出码 0 表示提供的图像数值通过，1 表示合法输入未达阈值，2 表示输入或写入失败。报告中的 `native_capture_origin_verified` 和 `desktop_or_project_acceptance` 始终为 `false`：独立 Agent 仍须核对实际网页与实例来源、固定修订、全部画面内容和掩膜／锚点充分性。哈希和采集标识不自行构成原生来源证明，不将本地测量通过记为五端桌面或真实业务验收。
