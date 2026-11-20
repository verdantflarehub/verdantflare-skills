# 本地执行入口

## 1. 用途与边界

`scripts/production_gate.py`连接上游要求、实际请求校验、提交和本地选段。它不是新增Studio协议、权限或审批系统。适用于已有基线的Video生成；Image等媒体继续沿专业技能执行，不虚称本入口支持全部媒体。调用前依当前用户范围核实是否允许生成；`check`完全离线，不消耗GPU。

## 2. 准备包

所有JSON严格解析，重复键、非有限数拒绝。包内文件均用`{"path":"相对路径","sha256":"文件字节摘要"}`引用；请求规范摘要另用`validate_reference_reuse.request_digest`，不可混用。被引用的请求、imports、audit、locks、action、camera、semantics置于packet同目录，素材可用相对路径引用，避免复制原资产。

上游`basis.json`由固定剧本／资产索引派生：`project_id`和`units`。每单元包含：

- `required_references`：沿用既有key、artifact_id、sha256、basis结构；即使packet删本地必需项，仍以此清单检查。
- `model`：必须精确匹配的route、model、quality_profile等实际字段；不为本地项目切换默认外部渠道。
- `provenance`：key到来源种类，白模代理为`blender_actor_proxy`，纯相机为`blender_camera`；种类在上游固定，代理只允许space／camera用途。
- `required_events`、`event_order`（前后事件ID对）及`final_state`：编剧／导演确认的因果和落态。
- `camera`：blend_sha256、parameters（位置／朝向／焦距／画幅等完整适用参数）、checkpoints事件名、moving布尔值。
- `review_requirements`：接画所需actors映射（每人original_identity与approved_look的文件摘要）、contact_shot和join_required。接画不能通过删人物、换身份或关闭接触检查弱化上游范围。
- `semantic_checks`：判断项到visual／sound依赖域。至少按镜头纳入身份、衣层、接触动作、空间、听者反应。存在同缺陷重复失败时加`repeat_defect`、`previous_core_sha256`，必须有方法改变判断；只改seed、标题或幂等键不算改方法。

调用者从独立上游索引传`basis_sha256`，不能临时由修改后的同一包自签代替固定依据。

`packet.json`含unit、basis_sha256和上述七份文件引用，可另带soundtrack文件引用（后期独立音轨）。实际请求内的音频／prompt变化仍影响表演，所以会使visual失效；只有独立后期音轨变化才复用未变视觉检查。

`action.json`含initial状态字典和有序events。每事件有id、requires状态字典、sets状态变化、description，可带after依赖。例：left_hand=seal时，“左手撑窗”要求left_hand=free会失败。系统不从文字自动推导物理真相；分析者须按[动作方法](../performance/action-analysis.md)写全状态和上游事件。

`camera.json`含blend_sha256、parameters、purpose=space_and_camera和checkpoints。每个检查点有outcome、具体observation、evidence文件引用；moving时另有path_reviewed=true及preview文件。预演是否真证明遮挡需助手看图／视频，程序只查绑定与覆盖。

`semantics.json`含checks字典：每项有fingerprint、outcome、reviewer、observation、evidence列表。用`load_inputs(...)[4]`取得依赖摘要，实际读图／读文后登记判断。任何输入或检查器变化都会改变摘要，不可批量把新摘要写回旧“通过”。程序不验证观察诚实，不能把填表当审片。

## 3. 检查与提交

先运行入口`check`。错误码指向具体缺项；成功只说明记录完整，`visual_quality_verified`恒为false。

Python调用方式（从本技能scripts目录导入）：

```python
from production_gate import MCPAdapter, submit

adapter = MCPAdapter(video_client_path)  # 专业Video技能实际安装位置
result = submit(packet, basis, pinned_basis_sha256, original_journal, adapter)
```

真实适配器复用Video客户端发现目录／Schema／能力，要求可用的video.preflight返回can_submit=true。按现行协议预检只排除idempotency_key；create使用原冻结arguments，不重新拼装或改变模型参数。缺失能力则阻断，不能切外部渠道或重建请求绕过。

预检后重验文件和语义绑定，独占创建journal，先持久化完整请求再create；成功写任务ID。请求异常保留submission_unknown；再次调用只恢复原journal，不再次create，也不因输入缺失阻止查询旧任务。未知回执无任务ID则返回reconciliation_required，由专业技能查原任务；不能删除journal重试。不同生成单元须独立journal，恢复结果不表示新请求已提交。

## 4. 接画

`adopt(report_path, selection_path, expected_selection_sha256)`复用`validate_visual_review`。report还须包含：

- 原origin_journal、basis文件引用、unit及request_sha256；current_review须绑定候选／请求／task_id，状态必须确为approved_video，不能自行填批准。
- playback.video与audio：kind分别为observed_video／observed_audio，coverage=complete_original_speed，candidate_sha256、outcome、reviewer、具体observation、evidence。播放器启动日志不等于观看／听审；无声片也须确认确实无异常音轨。
- observed_events：上游每个事件对应实际frame、observation及evidence，检查顺序。动作自然度另由真实观察判断。

selection包含固定basis_sha256。入口检查上游／任务来源、选段清单摘要及并发锁，保留原清单备份后原子替换takes[unit]。失败保持清单不变。后期装配须读取该清单的候选路径及证据，完成重建验片；入口不渲染影片，不新增用户批准。

## 5. 验证与局限

`tests/test_production_gate.py`使用明确标记的离线替身验证副作用次数、故障拦截、恢复和接画。假素材不能证明真实审片或模型输出质量；真实接口能力也须当次发现。脚本无法阻止绕开入口，执行后须核对实际任务回执覆盖；已有未知任务不重复提交。
