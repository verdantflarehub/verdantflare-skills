# 页面内容关联与核验

## 1. 先区分故障层

打开原项目最新修订，读取 `domain_documents` 中唯一 `project-content`，核对实际 category 与服务支持的 Schema。现有 `video` 项目不因技能适用于短剧而自动迁为 `ai_short_drama` 或 `creative_video`。

分别查明文件是否已归档、领域对象是否引用、字段是否被当前页面消费、真实 MIME 是否支持该播放器。只有名称和 overview 中的本地路径，不能代替媒体引用。Manifest 有文件不等于对应页面有内容；前端不支持的槽位也不能靠塞入错误字段解决。

## 2. 现有 video 分类

- 项目封面用 `cover_file_id`；可播放的当前交付候选用 `video.output_file_id`，注明真实候选状态。该展示引用不等于 manifest.selections、人工终审或 World 发布，保留既有选择与批准记录。
- 每个已产出故事板的 `image_file_id` 绑定同一镜头的真实图片／抽帧。`reference_file_ids` 只放参考图片；`result_file_ids` 只放对应结果视频；配音、方案、记录及其他文件归 `file_ids`。不要把 WAV、JSON、MP4 放进参考图，也不要把 PNG 放进视频结果。
- 故事板的当前结果与用户明确使用的制作版／剪辑来源一致，依摘要和固定引用核对。保留旧候选但标明历史；未经批准的新版不因编号较大自动取代现用版。页面卡片顺序按当前叙事，回顾重复使用的镜头不冒充新案例。
- `video.cast` 使用真实角色条目与单人身份图；已有同项目图片可绑定 portrait_file_id，不编造来源角色项目。多人拼图不得作为每个人的头像。
- 总体配乐、完整旁白与制作文档使用该分类已有文件预览入口；记录具体查看位置。若有明确的专属页需求而当前 Schema／前端不支持，作为设计或实现缺口处理，不制造“配乐故事板”或未知字段。

## 3. 保存与证据

先备份固定基线，形成目标→字段→file_id→ContentRef→实际 MIME 的映射，提交前检查不丢历史、不改变无关选用。抽帧是派生封面，保存来源视频摘要和时间，不重新生成视频。依赖服务器分配新 file_id 时，可先归档派生文件，再基于返回修订提交完整业务关联；中间状态仅报告已归档。

回读固定修订和领域 JSON。根据 Artifact 返回的 metadata 核对 MIME／摘要，再按本次变更范围受控下载实际字节并解码。缩略图、角色图片、主交付媒体和修正后的媒体类型需覆盖；复用已核验的不可变版本无需重复全量下载。

可用 `scripts/check_video_display.py --manifest open.json --content project-content.json --versions metadata.json` 做只读关联检查。versions 为 file_id 到 Artifact 实际 version 元数据的映射，不能用扩展名猜 MIME。只有本轮交付确需成片或完整卡片时添加 `--require-output`、`--require-covers`；用 `--expect-unit`、`--expect-cast` 核对明确的对象清单。校验器不执行上传、不创建批准，也不替代当前 Schema、权限、字节解码或浏览器渲染验证。

MCP 核验给出关联、类型、内容可读的证据，不能声称看到了真实页面。明确进行前端修复／页面验收的任务按宿主规范另做真实页面检查；普通项目同步无需额外浏览器操作。
