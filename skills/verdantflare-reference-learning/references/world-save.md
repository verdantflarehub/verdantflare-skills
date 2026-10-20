# 独立 World 保存与恢复

## 1. 先确认本次操作的能力

沿[MCP 入口](mcp-results.md#1-发现入口与身份)发现实际工具与 inputSchema。独立保存需要 `world.draft` 的 create/get/commit/status，以及 `artifact.write` 的 world_draft_id 来源和 `artifact.read` 的草稿固定修订作用域；需要登记资产时另核对 `world.register` 的 source_draft_id、`world.commit_status` 与 `world.get`。工具名称存在不证明其 Schema 接受新字段，格式文件也不证明服务已经上线。

缺登记能力仍可在已支持时保存并使用草稿；缺独立保存能力则保留本地可编辑文件及摘要，说明未同步。旧项目接口不接受草稿参数，不创建占位项目、不调用下游内部地址。既有项目创作继续由 Project 技能负责，不通过本流程迁移或脱离原项目。

## 2. 准备可编辑成果

本路径新工作使用 collection（仅收录）或 learning（分析结果）；创建后 purpose 不变。契约中的 creation 保留旧内容读取与恢复兼容；用户要求修改或创作时转真实 Project，不以新建 creation 草稿代替。仅收录 URL 可保存标题、网址与笔记，但不能标为已经抓取或分析。分析固定 collection 的修订作为来源，保存到 learning；无需先登记资产，也不改写 collection。

类型化正文采用 `document_kind=reference-learning,schema_version=1`，显式提供 origin、sources、coverage、results、relations、uses；空集合写 `[]`。来源角色、事实／推断／新创作、完整覆盖和模型验证状态按实际证据填写。`results` 保留各自 type_key/type_version、正文 payload、来源与使用条件；具体字段按包内[中央格式投影](learning-contracts.generated.json)的 reference-learning `$defs` 按需查询，尤其是 result、prompt、variant 和所需类型。不要用同一个通用 payload 替代所有类型。

可在已具备 Python 3.10+、jsonschema 4、referencing 的环境使用包内检查器，从技能根目录执行：

```sh
python3 scripts/check_learning.py --input path/to/learning.json
python3 scripts/check_learning.py --schema world-draft-manifest --input path/to/manifest.json
```

命令中的路径替换为本次实际文件。检查器只验证本地 JSON 及结构，拒绝重复键与非有限数字，不执行内容或获取外部 Schema；依赖缺失时报告，不能默认安装。引用是否存在、权限、局部关系一致性、真实分析覆盖和实际模型／工程执行由服务及对应任务另验。即使输出 PASS，也不能写“全部拆解完成”。

清单使用 `schema_version=1,kind=world-draft`，保留服务返回的 draft_id、purpose、name、subjects、files、domain_documents、source_refs、asset_refs、run_refs。每个 files 条目有本地稳定 file_id、相对 path、role 和真实 ContentRef；领域入口为 `document_type=reference-learning` 与对应 file_id。来源引用固定到草稿／项目修订或资产版本、文件和需要的 object_key；必需资产依赖写入 asset_refs。不要把语义字段塞到管理工具顶层。

## 3. 保存和固定回读

1. 沿用已获授权的保存目标和现有工作区。新建时先在任务恢复记录固定 create commit_id、后续 write_id、commit commit_id 及文件 ID；新请求 ID 使用 UUIDv7，与服务生成的 draft_id/revision_id 分开。记录包含原请求、正文摘要及各阶段回执，不含凭据。调用 `world.draft(mode=create,name,purpose,subjects,commit_id)`；空草稿创建成功还不代表内容已保存。
2. 通过 Artifact 技能写入实际字节：source 使用 `{kind:user_edit或user_import,world_draft_id:真实草稿ID}`，不同时传 project_id，不伪造 task_output 或内部清单来源。小文本按 text，大文件按 prepare/PUT/commit；核对返回的来源、ContentRef、大小和 SHA-256。
3. 基于固定父修订完整清单合入新文件及明确的领域入口、来源和依赖。保留无关文件、引用与未知扩展；不能只提交本次文件列表而意外删除既有内容。调用 `world.draft(mode=commit,draft_id,expected_revision_id,commit_id,manifest)`。
4. 提交成功后用返回的 draft_id/revision_id 调 `mode=get` 固定回读；按该修订文件集合，以 `artifact.read(mode=text或download,content_ref,access:{world_draft_id,world_draft_revision_id})` 核对正文及摘要。关联来源须使用其本来有权的固定作用域，不能拿当前草稿 ID 去读任意 ContentRef。

正文大于 Artifact 文本上限时改用受控文件传输；超过清单或领域限制时按业务结构拆分，不截断后声称已完整保存。媒体原生运行身份沿真实回执保留；更强的模型执行证明尚未被服务支持时报告 CONTENT_NOT_READY，不能删去来源、改写成已验证来强行保存。

## 4. 按需登记与立即使用

用户只要沉淀收录或保存分析时，到固定草稿即可；用户要资产入库时选择明确文件及必需依赖，保留原草稿。调用 `world.register`，使用稳定 commit_id、source_draft_id、source_revision_id、file_ids、name、asset_type、subjects、relation、depends_on。独立原创按实际上下文用 produced_in，整理或导入用 curated_in；这不代表版权或质量审核结论。

新建不传 asset_id 或 expected_asset_version_id；明确更新已有资产时，先核对写权限并固定其当前版本。返回资产 ID／版本后调用 `world.get` 固定回读，再按资产作用域读取选中文件。清单版本 2 的 source 指向草稿，但不会赋予资产读者对来源草稿或未选中文件的权限。

登记选中文件须满足领域正文的文件及资产依赖；遗漏时先报告具体缺项，不默认追加私人笔记。公开范围不因源工作区里还有其他内容而扩大。从项目明确沉淀提示词前，按[模型提示词](model-prompts.md#4-使用验证与沉淀)处理来源与新验证状态。

立即参考或复制可读取固定 learning 草稿或已登记资产。修改和创作先进入真实项目，向生产技能交接固定来源、选定成果／变体、目标变化、输入绑定和真实项目目标；生成结果先保存 Project，经用户明确选择沉淀后再登记 World。读取／复制不触发生成，生成成功必须有真实运行及输出；保存方法候选不自动安装技能。

## 5. 失败后恢复同一次操作

- 草稿创建／提交响应未知：`world.draft(mode=status,commit_id)`；committed 使用原 result，preparing 沿原请求恢复。NOT_FOUND 不证明请求未到达，只能重发原请求与原 ID，不创建替代草稿。failed/conflict 保留正文，核对最新状态后再为修正后的新意图生成新 commit_id。
- Artifact 响应未知：按其原 write_id/upload_id 恢复，不重新创建草稿；已知内容不可变版本不重复上传。
- 登记响应未知：新建时 `world.commit_status(commit_id)`；更新已有资产带原 asset_id。已 committed 从原 result 回读，不要求重新读取已无法访问的来源草稿；preparing 继续原请求。明确 failed/conflict 才在解决原因后建立新登记请求，已保存草稿可复用。
- CAS 冲突：保留原正文和请求，读取实际新 head，核对完整清单和差异后再提交；不把 expected_revision_id 机械改成最新值强行覆盖。
- 读取／权限失败：保留固定引用和阶段，不更换身份、换到来源草稿或伪造 Project。撤权返回处理中时不宣称已撤权，服务完成提交终态恢复后重试原撤权。
- 已保存派生正文与继续使用来源依赖分别授权。原资产撤权后，不靠派生关系回读原资产；自己的派生正文可读，也不代表后续依赖消费已经获权。

持续失败时保存当前可用成果、原请求及恢复入口，报告具体不可用阶段。不要无限重试、重新分析或重复提交生成任务掩盖保存问题。
