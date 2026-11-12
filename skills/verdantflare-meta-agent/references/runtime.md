# 本机协作运行时接口

## 1. 当前依据

- [规则约束](01.base-rules.md)：身份、消息与恢复语义。
- [运行入口](../scripts/agent_runtime.py)：CLI 参数。
- [持久存储](../scripts/verdantflare_agent/store.py)：事务、身份与状态转换。
- [Worker](../scripts/verdantflare_agent/worker.py)：持久连接与结果观察。
- [交接样例](../resources/handoff.example.json)：登记格式，样例本身不构成授权。

## 2. 数据库与服务配置

Python 3.10 及以上，标准库即可运行账本及测试。数据库路径由调用方显式提供，使用当前操作系统用户控制的本地目录，不提交 Git，不放云同步目录。数据库绑定解析后的工作区真实路径，另一个工作区不能借用同一数据库。

同数据库只能运行一个 worker，操作系统文件锁阻止第二个存活 worker 启动；崩溃释放锁，保留 SQLite 及审计事件。删除 lock 文件不是解锁方式；不能通过删除数据库绕过不确定状态。工作区迁移需核对外部任务和路由，不能简单改数据库中的 workspace 字段。

run 的输入为 `services` 对象，键为已登记服务标识，值含 `agent` 与 `argv`。agent 仅接受 codex、claude、antigravity、grok；argv 是显式字符串数组，启动对应 MCP stdio 服务，不能使用 shell 拼接命令。配置中的路径和本机信息留在受控配置；消息不能提供或覆盖 argv。配置缺少已实现的适配模块时返回 ADAPTER_NOT_IMPLEMENTED，不降级到其他 Agent。

本运行时不注册开机启动、不全局安装、不改宿主权限。通过已授权的进程管理方式保持 run 存活；进程已退出就不能宣称继续自动回传。

## 3. 登记与查询

以下命令中的路径为通用示例，实际数据库放目标环境批准的本地状态目录。

```text
python scripts/agent_runtime.py --workspace . --database .tmp/agent/state.sqlite register --input handoff.json
python scripts/agent_runtime.py --workspace . --database .tmp/agent/state.sqlite sessions
python scripts/agent_runtime.py --workspace . --database .tmp/agent/state.sqlite status --message-id message-1
python scripts/agent_runtime.py --workspace . --database .tmp/agent/state.sqlite run --input services.json
```

register 使用交接检查器的完整对象，验证文件与路径后登记端点和委托。首次 revision 必须为 1，返修依次递增；同修订、同内容可幂等重复。稳定任务键和计划身份不能随返修改变。

sessions 返回原生地址及 display_name；这里的名称是逻辑名，不代表宿主标签已改。rename 输入 session_key、name，仅改变本机逻辑名。

bind 输入 session_key、receipt 和 expected_turn_id。receipt 含真实 session_id、task_id、turn_id；首次绑定的 expected_turn_id 为 null，更换已有绑定时必须匹配先前轮次。bind 只登记已获得的后端收据，不创建会话；适配器调用前还会向服务核对会话、模式和当前轮次。

relink 输入 plan_id、plan_name、plan_path 和 tasks 数组；每项任务含 task_key、task_label、change_path。它核对新文件存在后更新当前映射，保留旧交接和消息快照，不递增任务修订或重新执行任务。编号变化后按需 rename 展示名称。

## 4. 消息、回执与结果

enqueue 输入消息对象，包含 message_id、kind、plan_id、task_key、assignment_id、revision、sender、sender_generation、recipient、recipient_generation、causation_id 和 payload。sender/recipient 是登记的 session_key，payload 是对象；类型和路由必须符合该委托。生命周期使用规则专册默认值，程序接口可显式配置。

status 返回持久消息、状态、尝试数、有效路由代数、后端收据及处理回执。包含任务正文的结果只在受控环境查看，对外报告须脱敏，不直接复制整个状态到 Git 文档。

ack 使用 --message-id 指定消息，输入 recipient、generation、assignment_id、revision、turn_id 和 action。必须匹配该次投递收据；相同回执幂等，异文回执拒绝，旧代数和旧修订只留历史。ack 不会产生新消息，也不意味着业务已验收。

report 使用 --message-id 指定已投递的 assignment/review_request，输入 terminal 和 result。terminal 为 completed、failed、cancelled 或 timed_out；result 是包含证据的对象。服务观察器使用同一事务入口，生成由委托、修订、外部任务、轮次和终态决定的唯一消息，避免执行方与观察器重复回传。后端完成不改变 business_accepted=false，业务验收仍由统筹核对。

## 5. 接管与错误恢复

takeover 输入 endpoint、expected_generation 和 authorization。endpoint 使用交接中的地址结构，generation 必须递增 1；authorization 记录真实接管依据，不能自造用户批准。

migrate 使用 --message-id 指定消息，输入 expected_generation。只迁移 queued/retry_wait 的明确未投递消息，原信封保持可追溯，实际 route_generation 更新。旧路由未迁移时 worker 留下 MIGRATION_REQUIRED，不自行选择替代会话。

delivering 租约到期变为 unknown，先核对原请求，不自动重新派单。已投递消息仅查询原任务；观察失败保留收据和游标，显示 OBSERVATION_UNAVAILABLE。等待人工审批不作终态，不自动批准。

失效的 CLI 输入返回非零退出码和固定错误码，不回显原始异常路径。日志、数据库与宿主结果检查分别进行；静态检查和模拟结果不证明目标宿主在线或全局安装完成。

## 6. Codex 适配

agent=codex 加载内置 Codex MCP 适配器。plugin 使用明确 thread_id 启动一轮，managed 必须先 bind 已有受管 task_id 和 turn_id，再调用 task_continue；不自动创建替代会话、不更改模型或权限。初始化专用受管会话按 Codex 调度技能执行，取得真实原生 ID 后登记。

外部写入前保存工具名和参数，request_id 在重试期间固定。queued 的 task_id 立即保存，turn_id 未确认前不记为 delivered。恢复时有收据先查原任务；无收据仅在服务同键幂等契约允许时重发完全相同参数，不因用户重命名、重新编号或配置变化生成另一个请求。

结果只读取收据对应的 turn_id，完整拼接分页并验证每页身份和游标推进。适配器要求接收方最终答复中提供匹配的 VF_AGENT_ACK JSON 行，普通“完成”或其他消息的回执不被接受。通知中的工作区和用户目录按占位符脱敏，原始结果保留在本机受控账本及后端。

逻辑名称通过 sessions 回读。原生命名使用 Codex 调度服务的 codex_session_rename，传入明确的 thread_id、cwd、name；原生 API 回读一致才返回 native。共享 rename 仍只改逻辑名，调用者分别维护两者，不把旧版服务的缺失工具隐式降级为成功。服务在线读取、真实受管投递和插件写入须分别验证；不能因为 capabilities 声称 read_verified 就报告插件写入成功。

## 7. Claude 适配

agent=claude 加载 Claude MCP 适配器。panel 使用明确 session_id；managed 有绑定时继续 task_id 和预期轮次，无绑定时以明确 session_id 恢复已存在会话，不创建替代目标。panel 的取消保留 UI 要求，不切换模式或自动批准权限。结果及回执沿用第 6 章的精确轮次和分页核验。

原生命名通过 claude_session_rename 独立执行，读取最新 customTitle 验证；共享 rename 仅调整逻辑名。受理连接须保持在线，未知后端任务保留原收据，不通过新建会话或改名掩盖。源码独立服务验收与已安装共享服务、面板 UI 验收分别记录。

## 8. Antigravity 适配

agent=antigravity 的 cli 模式使用 agy_cli_task_start/status/result，恢复明确 conversation_id；任务在源码服务的持久账本和后台 worker 中执行。服务合成的 turn_id 对应唯一请求，原生会话 ID 必须回读匹配。取消没有可验证原生终态接口时拒绝，不靠杀进程冒充成功。

panel 模式使用既有 Hub 工具，提交前冻结基线与提示词；响应丢失后只读核对 source=hub 的原生步骤。唯一匹配输入之后、下一条用户输入之前的回复才属于该轮；重复匹配或游标变化拒绝结项。没有匹配输入也不能证明未提交，因此不自动重发。会话忙碌不改派到 cli，焦点保持 focus_panel=false。

逻辑名称在 sessions 与交接头展示；当前未验证 Antigravity 原生改名入口，不声称宿主标题已修改。CLI 工作目录由进程 cwd 与 --add-dir 指定，原生历史归属未由 CLI 输出独立证明；使用明确登记并已核对来源的会话。

## 9. Grok 适配

agent=grok 使用 ACP 或 headless 的明确 session_id。有后端任务绑定时以 expected_turn_id 续接，无绑定时恢复指定原生会话，不新建替代目标。headless 续接使用原生 --resume，--session-id 只用于新会话指定 ID，不能混用。结果与回执按照第 6 章核验。

服务把新任务及续接固定给受理 worker；未领取命令才允许 owner 丢失后恢复。已提交未知结果继续占用工作区与会话，不用 cancel 将未知强行标为完成。受理连接必须保持在线；退出后的未知任务先核对原始终态证据。

逻辑名称通过 sessions 展示。ACP 专用初始化提示词第一行可作为 AFK Pilot 的 autoName；同步保留现有 customName 和 pinned。元数据与三份原生轨迹存在证明注册和可检索条件，不单独证明 UI 已渲染。headless 会话不作为 AFK Pilot 面板展示验收。
