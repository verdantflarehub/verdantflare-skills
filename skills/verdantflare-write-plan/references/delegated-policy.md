# 委托政策证据校验

## 1. 范围与边界

[政策模块](../scripts/delegated_policy.py) 校验不可变政策、独立原生审阅及调用方另行提供的当前目标捕获。返回值只含完整性元数据，不签发权限，不代表 CP0／CP1 通过，不替代 H1、H2 或发布授权。[登记加载器](../scripts/delegated_registry.py) 和[原生目标提供方](../scripts/goal_provider.py) 已接入 [主门禁](../scripts/plan_lint.py)；[过程收据模块](../scripts/process_receipts.py) 支持固定子进程的 CP0／CP1 技术执行记录及当前状态核对。[最终决定模块](../scripts/final_decisions.py) 与主门禁支持版本绑定审核、取消决定和活动归档预检，[迁移模块](../scripts/archive_relocation.py) 支持已移动文件与原始证据关联。尚未建立真实项目政策登记，不能把局部模块单独作为放行命令。

## 2. 输入契约

`validate_policy_evidence` 接收工作区根、政策与封套的相对路径／SHA-256 引用，以及调用方从实际计划取得的仓库 ID、计划 ID／路径、全部作者会话、当前核心对象路径和评审切片哈希。当前原生捕获及目标服务观察通过单独参数传入，不能省略后回退使用政策中的历史文件。

当前捕获必须完整保留已审捕获的原始字节前缀，包括不进入人类历史摘要的自动续跑和人工消息伴生行；允许追加，不允许裁剪、重排或重新序列化。当前目标观察的 `updatedAt` 不得早于历史已审观察，允许后续用量更新推进时间。两次独立来源校验通过不能替代这两个连续性条件。

政策 JSON 版本为整数 `1`，固定字段为 `schema_version / policy_id / repository_id / plan_id / plan_path / archive_plan_path / author_sessions / authorization / reviewed_user_history / allowed_gates / retained_gates`。授权含 `provider=codex-user-goal-v1 / session_id / event_sha256 / capture / observation`；两个文件引用固定为 `path / sha256`。政策本身不含封套或评审输出。捕获契约见[目标来源](goal-evidence.md)。

委托门禁只允许 `UI1 / CP0 / CP1` 的非空子集，保留集合必须完整包含 `H1 / H2 / production / new_cost`。活动计划与归档同名映射必须一致。版本、JSON 字段、重复键、重复门禁、路径别名、外部符号链接与超限文档均严格检查。政策及封套最大 256 KiB；来源文件使用原生收据及目标模块各自的读入上限。

## 3. 独立审阅封套

封套固定为 `schema_version / policy_sha256 / receipt`。`receipt` 引用有效 T2 工程角色的[原生收据](native-receipts.md)，其核心对象须与调用方实际对象一致；输入必须含政策、历史捕获和目标观察的准确字节。审阅会话不能属于任何政策作者或授权来源会话。版本 1 只消费已支持的 Antigravity Hub 工程角色，不将 Claude 架构结论当作工程细节审。

原生终态回复必须仅为一个完整 JSON 对象：整数 `schema_version=1`、`verdict=scope-confirmed`、准确 `policy_sha256 / plan_id / authorization_sha256 / reviewed_history_sha256`、布尔 `provider_readback=true`，以及按字典序排列的完整 `retained_gates`。代码围栏、普通“通过”文字、额外字段、同意另一政策或未回查来源均拒绝。封套位于审阅之后，故不作为自己的审阅输入。

原生校验返回的收据摘要必须与封套实际读取的收据字节一致；读取期间被替换时拒绝，不能把一个文件的正文与另一个文件的校验结果拼接。

`provider_readback` 是独立审阅者对实际回查的责任声明，校验器只验证其与原生回复的绑定，不能证明本地来源文件不可伪造。真实运行仍须由提供方适配器获得最新记录并由非作者回查；合成夹具不作任何真实授权。原生收据始终绑定原活动路径，归档按第 9 节验证实际迁移结果后重放原始字节，不能仅传归档路径便宣称迁移已验证。

## 4. 规则码与测试

- `PL-DELEGATE-SCHEMA`：不支持的 JSON、字段、版本、路径格式或摘要。
- `PL-DELEGATE-BINDING`：仓库／计划、作者集合、门禁范围、政策摘要或文件引用不匹配。
- `PL-DELEGATE-SOURCE`：历史／当前目标来源缺失、变化、暂停、替换或未审的新用户输入。
- `PL-DELEGATE-REVIEW`：原生审阅缺失、陈旧、对象不符、自审或范围结论不完整。
- `PL-DELEGATE-RECEIPT`：执行进程失败，或过程收据、输入、工具、评审、锁卷及命令不匹配，CP1 不能消费无效 CP0 记录。
- `PL-DELEGATE-H2`：最终审核／取消对象、版本、原生提问或用户答复缺失、不匹配或已被后续人工输入改变。
- `PL-DELEGATE-ARCHIVE`：归档映射、计划身份、原始字节、确定性迁移结果、关联集合或前后文件不匹配。

[合成回归](../tests/test_delegated_policy.py) 覆盖上述拒绝路径，独立于具体仓库授权。全量测试命令沿技能入口；局部通过不等于实际政策已登记或主门禁已升级。

## 5. 登记索引与实时来源

`load_registry(root, repository_id=...)` 读取固定位置 `docs/plan/.delegated-policies.json`；仓库 ID 必须由上层从实际 Git 仓库取得，不能采用政策自述或命令行替代值。文件缺失返回空登记；显式文件损坏、悬空链接、外部符号链接、旧摘要或任一政策冲突均拒绝，不因所选计划不同而跳过坏条目。版本 1 顶层为 `schema_version / repository_id / policies`，每条仅含 `policy / envelope` 两个摘要引用；独立检查重复政策 ID、计划 ID、活动／归档路径和授权来源选择器。`select_registration` 要求稳定计划 ID 与活动或同构归档路径同时匹配；返回的是登记引用，尚未验证原生封套或当前授权。

`await read_codex_goal(session_id, workspace)` 从受控运行环境发现已安装 `codex`，启动专用无窗口 stdio app-server；仅发送 initialize、thread/read 和 thread/goal/get，没有启动／恢复会话、发送 turn 或修改目标的能力。提供方返回的会话及工作区必须匹配；原生日志须位于当前 Codex 数据目录的 sessions 或 archived_sessions，拒绝逃逸。可执行文件读取前后摘要保持一致，传输逐请求匹配 ID，交互请求、超时、通知洪泛、无效／过大 JSON 均拒绝。错误不输出提供方正文或私有路径，进程始终由调用方清理。

日志最多 512 MiB，单行和原生 RPC 帧最多 16 MiB，选取捕获沿目标模块的 16 MiB 限额。流式扫描保留首条 session*meta、全部用户消息及 user_message／thread_goal*\* 事件的原字节；恢复会话的后续 metadata 逐条校验会话与工作区，不重新排列或重写原始首行。未知目标事件保留给严格解析器拒绝。捕获前后相关字节或文件身份变化则本次读回失败；不影响用户历史的助手追加允许。输出含私有捕获、目标观察和可执行文件摘要，默认不落盘、不在 repr 展示内容，不跨门禁缓存。

提供方只获取当前来源；上层仍须将其送入政策验证并核对原生授权、当前用户历史、独立封套、所有技术要求与最终 H2。新用户输入导致旧政策失效时，由 Agent 重新做范围审阅，不自行替换历史摘要宣称授权有效。原生日志是本地证据，不是不可伪造的远端签名。

测试见[提供方回归](../tests/test_goal_provider.py)和[登记回归](../tests/test_delegated_registry.py)。合成测试仅证明拒绝与解析行为；实际提供方可用性另作只读探针，不能代替目标产品验收。

## 6. 主检查器行为

所有门禁在提前返回前检查显式登记；文件不存在或有效索引未选择当前计划时沿旧行为。选中政策须同时匹配计划文首唯一 `计划 ID` 和实际路径，仓库 ID 仅从实际 Git origin 取得，`--repo-id` 不影响委托身份。草案只检查结构和身份，不读取提供方，不构成授权确认。

CP0 从实际计划关联文档推导主设计、唯一考卷、当前 T2 切片及 T1／T2 全部作者来源，通过实时提供方、政策封套及原有技术检查后，再读取一次来源并比较登记、计划、核心文档、评审和已有 T1—T4 作者变更的摘要。缺来源、未知作者、未委托门禁、前后变化均失败。普通 `check` 返回 0 只证明当次检查通过，不生成不可变过程收据；过程证据通过第 7 节的 `record-gate` 产生。

已登记计划的 CP1 必须先验证当前 CP0 收据并重跑 CP0 技术条件，才允许以其锁卷替代旧人工 CP0 文本来源；继续验证原有 CP1 条件，并再次读取来源。没有有效收据时返回 `PL-DELEGATE-RECEIPT`，不回退人工文本。活动计划归档预检按第 8 节核验真实 H2，实际迁移按第 9 节核验；自写“已废弃”不能代替第 10 节的真实取消决定。

[主入口合成回归](../tests/test_delegated_gate.py) 使用真实临时 Git 仓库及合成原生来源，验证校验链实际连通、提前返回防绕过、命令行身份隔离、失效来源、输入变化及旧流程兼容；夹具不登记到真实项目。

## 7. 过程执行收据

```text
python scripts/plan_lint.py record-gate docs/plan/YYYY.MM.topic.plan.md --gate cp0 --json
python scripts/plan_lint.py check docs/plan/YYYY.MM.topic.plan.md --gate cp1 --json
python scripts/plan_lint.py record-gate docs/plan/YYYY.MM.topic.plan.md --gate cp1 --json
```

`record-gate` 只适用显式登记且来源可核验的复杂计划。它执行当前 Python 与固定主检查器的严格 `d4a` 子进程，实际观测退出码、起止时间、运行 ID 和标准输出；超时为 150 秒，输出限 256 KiB。超时／中断只清理本次创建的进程树，Windows 使用无窗口子进程。只有真实退出 0、输出为对应计划／门禁的完整成功对象，且前后政策／来源／输入一致，才持久化技术收据。命令不修改计划，不注册政策，也没有跳过校验、指定任意执行程序或覆盖收据的参数。

输出、观察和收据各自按 SHA-256 排他写入 `docs/acceptance/process/<sha256>.json`，允许复用字节完全相同的既有文件，禁止覆盖和符号链接目录。收据引用观察，观察引用成功输出，不形成循环；原生用户正文不进入这些文件。执行者将 CP0 输出路径回填计划文首唯一 `CP0 技术收据` Markdown 链接。内容寻址和摘要证明本地字节关联，不是提供方签名，也不能替代非作者检查原始执行。

状态绑定实际仓库／计划／政策、授权事件和已审历史、锁卷、计划范围、设计／考卷路径、所有计划直接链接的设计与原型字节、当前有效评审及其原生收据摘要、全部本技能 Python 源码指纹；CP1 另含 T4 切片、全部 T1—T4 作者身份及所消费 CP0 引用。新过程收据要求 T1 双角色、T2 工程角色、CP1 的 T4 双角色原生收据，不把旧人工三元组升级为新的执行证据。未登记计划的旧兼容路径保持不变。

计划范围取第 1—4、8 章、计划 ID／负责人／等级和全部任务定义，仅排除 CP0／CP1 检查点内、代码围栏外精确独立的已知“当前结论”状态行；任务勾选、完成计数、恢复／审批追加及文首收据链接可正常更新。考卷第 2 章锁卷及 T4 证据切片各按既有规范化规则计算。新增需求、契约／原型变化、新评审替换、工具变化或锁卷变化使旧收据失效，不能只换 JSON 中的哈希。CP1 消费时同时重查全部当前技术条件和最新来源，不只读历史 `technical-pass`。

[过程收据测试](../tests/test_process_receipts.py) 覆盖无人工 CP0 的完整合成链、当前技术拒绝、跨范围／工具／评审／来源失效、内容寻址与失败不签发；另以真实本机子进程测试固定执行和退出观测，测试夹具均不代表项目真实验收。

## 8. 最终审核与活动归档预检

```text
python scripts/plan_lint.py prepare-final-review docs/plan/YYYY.MM.topic.plan.md --manifest delivery/manifest.json --release-id example-r1 --json
python scripts/plan_lint.py check docs/plan/YYYY.MM.topic.plan.md --gate archive-preflight --json
```

先将有效 CP1 过程收据填入计划文首唯一 `CP1 技术收据` 链接。准备命令重查 CP0／CP1、当前政策及原技术条件；manifest 的 `release_id` 须匹配命令参数，准确文件摘要须已进入当前 T4 工程原生评审的输入。通过后只生成内容寻址审核对象和提问文本，不修改计划、不发送提问、不批准。生成对象沿用 `docs/acceptance/process/` 的排他存储，计划以唯一 `最终审核对象` 链接引用。

提供方可选 `include_decisions=True` 保留完整助手消息，同时返回原用户／目标投影，读取前后两者都必须稳定。审核对象绑定准备时用户捕获的完整字节前缀、原授权与已审历史、仓库／计划、唯一请求 ID、版本、manifest 和 CP1。原生最终回复必须完整匹配生成文本且为 completed／final_answer；随后首个真实用户答复只接受独立“通过／审核通过／同意／批准”和末尾句号或感叹号，并核对相邻原生 user_message 正文。否定、附带条件、额外范围、无回复、评论区提问、助手自答、另一请求和后续人工撤回均拒绝。

最终答复增加的用户事件完整保留在当前来源中；只有准确绑定的最终决定分支才分别校验原已审历史与完整当前历史，不重写政策。原 CP0／CP1 收据继续绑定原已审历史，普通过程门禁不会因这个分支自动接受新用户事件。活动归档预检重新消费两份过程收据，实跑原 CP0／CP1 技术检查、原归档收尾条件，并在末尾复查最新来源和 manifest；只有这条链完整通过才替代旧人工 CP0／CP1 文本要求。既有无登记流程保持原行为。

取消使用第 10 节独立路径，实际迁移使用第 9 节映射。全部测试均为明确合成批准，不能当作真实用户批准。覆盖见[最终来源测试](../tests/test_final_decisions.py)、[最终门禁测试](../tests/test_final_gate.py)，以及政策和提供方回归。

## 9. 同构归档与原始字节

```text
python scripts/plan_lint.py prepare-archive docs/plan/YYYY.MM.topic.plan.md --json
python scripts/plan_lint.py check docs/archive/plan/YYYY.MM.topic.plan.md --gate archive --json
```

准备命令要求显式政策、真实最终决定和活动预检通过，成功交付计划全部任务已完成；取消计划保留未完成复选框。它从实际反向关联推导同计划的 plan／review／acceptance／已结项 change 移动，并扫描仓库 Markdown 入链。只允许对应分类的同构路径和相对链接修正；原位文件仅在链接确需修改时登记。目标碰撞、符号链接、未关闭 change、不支持的链接结构、超限输入均拒绝。受影响单文件原字节上限为 128 KiB，总计 32 MiB、最多 2,048 项；最终内容寻址 JSON 仍受 256 KiB 限额约束。

命令返回迁移描述引用、归档路径和唯一 `归档映射` 文首行，不执行移动、不自动勾选任务或修改完成状态。原 Markdown 以独立内容寻址记录保留，映射引用原记录和确定性转换结果摘要，包含当前工具指纹及 `delivery / cancellation` 结果类型。移动由收尾执行者完成，归档计划按命令返回值插入文首行，其余字节必须与转换结果一致；不能借链接修正夹带正文变化。

实际 `archive` 先核对实际身份、路径、原记录、物理目标、活动副本清零、完整关联集合及全仓入链，然后使用[只读证据视图](../scripts/evidence_view.py)在进程内重放原逻辑路径。视图只接受已经核对的 Markdown 原文，不修改文件系统或系统 API，不复制私有原生捕获；政策、来源、工具、媒体及其他输入始终读取当前文件。内部重新执行完整活动归档预检，包含当前原生用户决定、政策与适用 CP0／CP1／评审要求；最后再核对真实文件和登记。视图在正常返回或异常时均撤销，不能给下一次检查沿用。

迁移本身不会更改原生收据、CP0／CP1 或 manifest 的摘要。旧字节能重放是因为真实迁移结果已证明与其仅差确定性路径修正，不是允许用任意历史副本遮盖当前修改。测试见[归档迁移回归](../tests/test_archive_relocation.py)。

## 10. 取消归档

```text
python scripts/plan_lint.py prepare-cancellation docs/plan/YYYY.MM.topic.plan.md --json
```

仅在用户实际要求取消时使用该准备入口。它核对当前登记政策、原生来源、独立范围审阅和两次读回，生成无版本制品或 CP1 的取消对象及确定提问文本；不发送、不改计划。取消对象同样通过文首唯一 `最终审核对象` 引用，实际原生答复须为“取消计划／确认取消计划／同意取消计划”；普通“通过”不能代替。

收到准确取消决定后，计划及全部关联 review／acceptance 标记已废弃，已完成 change 保留完成，未完成 change 标记已废弃。活动预检保留原结构与状态一致性检查、实时政策和取消来源，不要求虚构技术成功或勾选未完成任务。后续 `prepare-archive / archive` 的映射必须为 cancellation，继续执行同一物理迁移核验和原始来源重放。取消不产生成功交付；新人工输入、反悔、假废弃状态或改称成功均拒绝。测试见[取消门禁回归](../tests/test_cancellation_gate.py)。
