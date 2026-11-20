# 原生评审收据绑定

## 1. 当前依据

- [技能入口](../SKILL.md)：范围与工具路由。
- [共享规则](01.base-rules.md)：角色、作者独立性和锁卷约束。
- [校验器](../scripts/native_receipts.py)：版本 1 字段及来源校验契约。
- [回归测试](../tests/test_native_receipts.py)：合成正例、拒绝用例及主门禁集成。

## 2. 适用边界

用于原生 Hub／CLI 不提供受管 `task_id / turn_id`、但可以取得唯一请求与终态结果时的证据绑定。现有受管三元组继续可用；`unavailable / unknown / none / null / pending / n/a / todo` 不是原生 ID。逻辑 assignment、修订号和本机请求编号不能改名为原生 turn。

工具验证文件完整性、请求／结果关联、当前输入及角色，不证明本地文件不可伪造。非作者须对照实际提供方读取原生记录，不能仅审核作者制作的 JSON。此功能不改变 CP0／CP1 的授权判据、锁卷、未闭环问题或最终用户批准规则，不等于委托过程门禁已经实现。

## 3. 描述文件

UTF-8 JSON 的 `schema_version` 必须为整数 `1`，不得有重复键或未知顶层字段。必填字段如下，拼写以校验器为准：

- `provider / agent / role`：仅接受 `antigravity-hub / Gemini / detail` 或 `claude-cli / Claude / architecture`。
- `plan_path / stage / review_hash`：工作区内计划相对路径、`t1 / t2 / t4`、当前报告的 `sha256:<64hex>`。
- `scope`：T1／T2 仅含 `design / acceptance` 相对路径，T4 仅含 `acceptance`。主检查器核对它们就是计划发现的核心对象，并按正式切片算法重算 review-hash；任意同内容副本也不能替换计划的真实对象。
- `session_id`：真实宿主会话；非空且不得为占位词。
- `inputs`：非空文件引用数组，每项只有 `path / sha256`，绑定交给审阅者的实际输入字节。路径不得重复指向同一文件；输入采用流式 SHA-256 校验。
- `request_sha256 / response_sha256`：原生输入和终态正文的 UTF-8 字节摘要，无 `sha256:` 前缀。
- `sources`：按提供方定义的证据文件引用，每个引用只有 `path / sha256`。
- `native`：提供方原生定位字段，见第 4 节。

输入和证据路径相对工作区，拒绝绝对路径、反斜线、冒号、父目录穿越及逃逸符号链接；只允许显式 `.gdrive/managed` 映射至受管盘。JSON 描述上限 256 KiB，每个来源文件上限 16 MiB，超限须明确失败，不静默截断后判通过。

输入摘要不取代主检查器的实时 `review-hash`。需签后追加的考卷／报告不作为自己的原始字节输入，避免自引用；保留实际交接的冻结材料，并由实时范围哈希检查设计／考卷冻结切片，签后区按既有规则处理。源代码和其他固定输入一旦改动，旧收据即失效。每份收据只描述一个角色的一次请求，不把两个运行拼接成成功证据。

原生版本 1 要求包括 T1 在内的 scope 与当前正式切片一致；阶段演进修改了该切片，须重新取得有效收据。旧文本三元组的 T1 历史哈希兼容规则不扩展为原生证据免检。CP1 同时重新核验 T1／T2 的原生收据，不能只看前置报告标题写了通过。

## 4. 提供方证据

### 4.1 Antigravity Hub

`sources` 仅含 `transcript / observation`；`native` 仅含 `start_step / end_step`，区间为左闭右开。保存 `agy_read_transcript(source=hub)` 的完整连续步数，其实际返回字段为 `source=hub_rpc`；不能混用本地 JSONL 行号或改写返回来源名。

校验首步为唯一已完成用户输入、末步为已完成且非空的 planner 回复；同时绑定输入／输出摘要。观测必须同会话、IDLE、总步数等于区间末尾，最新输入／回复与所选事件相同。缺步、混入另一请求、旧回复或未结束均拒绝。导出时剔除思考与工具参数，仅保留判定所需字段，并由非作者回读原生源核对导出一致性。

### 4.2 Claude CLI

`sources` 仅含 `stream / journal / observation`；`native` 仅含 `request_id / message_id / result_id`，分别是真实用户 journal 事件 UUID、最终 assistant message ID 和 result UUID。

`stream` 为原始 JSONL 输出，最后必须是唯一成功 result，`is_error=false`、`terminal_reason=completed`；最终 assistant message ID／正文须同时匹配 journal 和 result。journal 中沿 `parentUuid` 回溯至指定用户事件，不得跳过另一用户请求、缺父节点或循环；工具回传和 progress 节点保留以证明完整链。

每个 stream 事件必须带匹配 session_id；每个 journal 行必须带匹配 sessionId，无 UUID 的元数据也检查会话。用户／assistant／progress／attachment 事件不得缺 UUID。父链中的 user 事件只能是关联此前 tool_use ID 的 tool_result，可附完整 `<system-reminder>…</system-reminder>` 文本；空消息、单独图片、普通文本或找不到原调用的工具结果均拒绝，不能仅看有无文本判断是否为新请求。

`observation` 只含 `session_id / exit_code / completed / stdout_sha256`，必须由实际进程回收记录得到；退出码为整数 0、completed 为 true，stdout 摘要匹配输出文件。没有历史退出观测就不能补造；另做实际复核并捕获新的观测。thinking／tool_use 消息不能冒充最终文本消息。

## 5. 报告接入与校验

报告第 2 章（架构）或第 3 章（工程）使用唯一的 `[native-receipt](相对路径.json)` 链接。身份必须与文首及正文一致；正文显式 ID 不能与收据冲突。提供了坏链接或坏证据时返回 `PL-REV-NATIVE`，不能退回旧三元组。

T4 一旦使用原生格式，报告第 2／3 章与考卷第 4.5 节必须提供完整双轨原生收据，不混用原生和旧三元组。第 4.5 节按 `- Claude：…`、`- Gemini：…` 各一个顶格列表项登记，两处引用的描述文件摘要及有效字段必须相同。重复归属、游离收据或另一个有效请求仍拒绝。作者与评审者身份／会话互斥、跨角色 ID 不重叠继续生效。

每份交付变更的实际作者必须有可归属的原生 `session_id / thread_id`；多作者逐人登记，不能用无归属的一条 ID 证明所有作者独立。缺失记录直接返回 `PL-REV-INDEP`，不把空集合当互斥证明。原生章节用明确的 `Gemini：…` 等角色声明，普通叙述中提到被审作者或其他 MCP 通道不算新增评审者。“审阅”“独立工程审”等纯评审职责不算实现作者；兼有修改、实现等职责时仍计入。

独立检查只用于排查证据，不能代替完整门禁：

```text
python scripts/native_receipts.py evidence/receipt.json --root . --plan docs/plan/YYYY.MM.topic.plan.md --stage t2 --role detail --review-hash sha256:<64hex>
python scripts/plan_lint.py check docs/plan/YYYY.MM.topic.plan.md --gate cp0 --json
python -m unittest discover -s tests -p 'test_*.py'
```

独立校验成功返回 0，证据不合格返回 1，参数错误由 argparse 返回 2。仅输出允许的元数据及通用失败原因，不执行证据内命令、不访问网络、不打印输入／回复正文、思考或凭据。测试中的合成会话和成功结果仅用于验证检查器，绝不能作为项目真实评审或验收收据。
