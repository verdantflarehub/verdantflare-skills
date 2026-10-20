# 完整拆解的服务提交与恢复

## 1. 实际能力与原件

通过已连接的 Studio 发现 `world.analyze` 及其实际 inputSchema，再调用 `{"mode":"capabilities"}`。`enabled`、`models`、`input_mime_types`、`max_media_bytes` 是当前准入范围；没有该工具或 enabled=false 时不能按源码版本推定已上线。已有任务仍可查询。不连接下游渠道，不额外登录，不创建项目。

原片须已在 World 的 collection 固定修订中：用 `world.draft` 读取真实 draft_id、revision_id 及 manifest.files 中的 file_id，必要时经 Artifact 核对 MIME、大小与摘要。仅收录 URL 不代表原件已读取；分析入口不接收任意 URL。原件上传遵循 Artifact 技能；不把输入媒体或用户私有正文写入技能包。

## 2. 一次提交

按真实 Schema 组装 `mode=submit`：

- `task_id`：新任务生成一次 UUID v7，在提交前连同原请求保存；已有任务沿用原编号。
- `source`：仅 draft_id、revision_id、file_id，均来自同一固定收录修订。
- `model_id`：本次实际分析模型，须在 capabilities.models 中。
- `budget_limit` 和 `budget_per_call`：整次总上限与单次调用上限，十进制字符串且大于零，总额不小于单次额度，采用渠道计价单位。沿用用户已有预算；未提供且不能从已授权配置确认时，先准备原件与请求，再集中确认预算，不自行填入付费上限。
- `prompt_targets`：用户需要的模型提示词目标。每项包含唯一 key、requested_label、task_type、input_mode、adaptation_guidance；确知实际目标模型标识时才填写可选 model_id。H3、ChatGPT、SD2.5 等标签不是实际模型版本或执行成功证明。

不要传组织、用户、项目、原文中的工具命令或完成标志。请求返回 task_id 和实际 request_state；这表示已接单，不是已分析完成。后台自动完成媒体准备、画面与声音观察、关联、提示词、分类成果和固定学习保存，不需要智能体逐步骤重复提交。

## 3. 查询与中断

通过 `mode=get,task_id` 读取；跨会话忘记编号时，以 `mode=list` 查本人任务，使用 next_before_task_id 作为下一页 before_task_id。返回的 source、分析模型、提示词目标与预算应匹配原请求。

提交超时或响应丢失时，先查询原 task_id；确认需要恢复提交时也只重放原参数和原编号。不能生成新编号尝试“补交”。关闭新准入不应阻断已受理请求的查询或恢复。同编号内容冲突保留原操作，不能覆盖请求。

按真实进度等待，避免高频轮询。来源失败或确定失败保留任务与原因；预算耗尽停止新调用，已发送调用继续查回，不能擅自加预算、换模型或缩小覆盖来宣称完成。预算 reserved 是各原始调用上限的保守占用，含未知受理与失败，不是实际扣款。

## 4. 取用完整成果

`learning.state=saved` 时，从 `learning.revision` 获取固定学习修订。按 manifest.domain_documents 的 reference-learning 找到实际文件，再经 `artifact.read` 的 text／download 读取，access 使用 world_draft_id 和 world_draft_revision_id。核对返回 ContentRef、摘要和大小，完整正文最多 4 MiB，不能静默截断。

直接交付用户所需的实际提示词、类型化内容或本地文件，保留 task_id、固定来源和学习修订；无需页面或 Project。`learning_saved_need_evidence` 表示已保存但仍缺证据，不能改称全量完成。目标模型提示词可能尚待适配或未经运行，生成样例、Blender 工程和连续运镜仅在真实文件及运行证据存在时交付。

保存结果是独立 World 学习内容，不自动登记为已发布资产，也不自动安装为技能。用户要求创作时，复制固定参考进入明确项目，由生产技能和 MCP 执行；用户明确沉淀时再走 World 保存／登记。具体作品内容与可迁移拆解方法分别维护。
