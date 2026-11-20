# Immutable lock-hash regression fixture

## 1. Source

Frozen Chapter 2 from verdantflare-design commit ca1af2ea; test data only.

## 2. Locked scenarios

本章在 `T1` 前置定义六项无环自举验收场景与反作弊红线，经 `T1 / T2` 智能体三段式评审闭环后预计算本章规范化 LF 文本的 `SHA-256` 摘要呈报 `CP0`；获 `CP0` 批准后将 `锁卷哈希` 写入计划第 7 章审批记录并将文首状态置为 `考卷已锁定`。在 `T3 / T4` 实施与实跑期间严禁删减场景、放宽阈值或硬编码短路，否则 `--gate cp1` 将以 `PL-CP1-LOCK` 自动阻断。

### 2.1 S1：新建与升级技能静态合规门禁、12 专册自包含与包级回归检查（归属 `T4.1`）

- **测试对象**：新建技能 `verdantflare-skills/skills/verdantflare-meta-plan/`，联动升级后的 `verdantflare-meta-markdown`、`verdantflare-meta-agent`，以及包级回归测试 `verdantflare-skills/tests/test_agent_and_meta_skills.py`。
- **执行入口与命令**：
  - 在仓库原位对三项技能运行 `lint_skill.py lint <skill_dir> --style numbered`；
  - 将三项技能分别单独复制至 `.tmp/rd-plan-governance/isolated-skills/<skill_name>/`（无兄弟目录隔离环境）再次运行 `lint_skill.py lint --style numbered`，并断言 `verdantflare-meta-plan/references/` 完整包含既定 12 份专册且零跳出技能目录的外部本地相对链接；
  - 运行 `python -X utf8 -m unittest verdantflare-skills/tests/test_agent_and_meta_skills.py`（已将 `verdantflare-meta-plan` 纳入 `SKILLS` 元组）；
  - 对三项技能内全部 `SKILL.md`、`references/*.md` 与 `resources/templates/*.md` 运行 `md_format.py --check` 与 `check_md.py`。
- **量化通过判据**：原位与隔离环境 `lint_skill.py` 退出码全为 `0`（`0 errors, 0 warnings`），`SKILL.md` 严格具备四节中文二级标题且正文 `<= 4000` 字符，包级测试全绿且 `skipped == 0`，全部专册与模板 `PASS: 0 errors, 0 warnings`。
- **反作弊红线**：禁止在 `lint_skill.py`、`test_agent_and_meta_skills.py` 或 `check_md.py` 中添加白名单跳过规则或放宽跨技能目录外逃链接禁令。

### 2.2 S2：`plan_lint.py` 五档门禁与 `check_handoff.py` 单变量变异单测矩阵（归属 `T4.1`）

- **测试对象**：`verdantflare-skills/skills/verdantflare-meta-plan/scripts/plan_lint.py`、`tests/test_plan_lint.py` 及 `verdantflare-skills/skills/verdantflare-meta-agent/scripts/{check_handoff.py,test_handoff.py}`。
- **执行入口与命令**：运行 `python -X utf8 -m unittest discover -s verdantflare-skills/skills/verdantflare-meta-plan/tests -p "test_*.py"` 与 `python -X utf8 -m unittest discover -s verdantflare-skills/skills/verdantflare-meta-agent/scripts -p "test_*.py"`，基于完整合法基线每次仅做**单变量变异**构造不少于 18 组独立正反例夹具（禁止以“规则码 A 或 B”模糊断言）：
  - **反例 F1（缺 TXT 原理图阻断 CP0）**：仅移除设计非空 `text` 代码块（或仅在代码块内伪造标记），断言退出码 `1` 且精确命中 `PL-CP0-TXT`；
  - **反例 F2a & F2b（配图失配阻断 CP0 与未挂载独立报错）**：`F2a` 使设计图片缺 `receipts/` 回执或内容 `SHA-256` 与后缀失配，断言退出码 `1` 且精确命中 `PL-CP0-IMG`；`F2b` 模拟 `.gdrive` 未挂载，断言退出码 `2` 且精确命中 `PL-GDRIVE-UNMOUNTED`；
  - **反例 F3a、F3b & F3c（CP0 评审缺失/未闭环、冻结切片陈旧哈希、违反反自审或越权传参分别独立阻断）**：`F3a` 缺 `t1` 或 `t2` 报告、或逐条 `[OPEN]` 与汇总行不一致，精确命中 `PL-CP0-REV`；`F3b` 修改 `t2` 被审设计正文冻结区使 `评审对象哈希` 失配（同时验证仅修改文首状态/日期或升迁 `.gdrive` 桶名仍放行），精确命中 `PL-REV-STALE`；`F3c` 在第 7 章批 `D4-A`（或未批 `D4-B`）时强传宽松模式、作者与评审助手重叠、替补者兼任同阶段双棒、试图拉 `Claude` 替补 `codex_detail` 工程细节审，或在批 `D4-B` 时会话 ID 重叠/漏标 `同源隔离会话评审：是`，分别精确命中 `PL-REV-INDEP`；
  - **反例 F4（缺预设考卷阻断 CP0）**：缺失反向关联本计划的 `accept_*.md` 或第 2 章无 `S1..Sn`，精确命中 `PL-CP0-ACC`；
  - **反例 F5a、F5b & F5c（CP1 证据缺失、T4 评审缺失或篡改冻结证据、T4 反自审分别独立阻断）**：`F5a` 残留“待 T4 实跑回填”或无 64 位 `SHA-256`，精确命中 `PL-CP1-ACC`；`F5b` 缺失 `t4` 通过报告命中 `PL-CP1-REV`，或篡改 `accept_*.md` 第 `2～4.4` 节冻结证据命中 `PL-REV-STALE`（同时验证正常追加 `4.5` 收据、第 `5` 章记录与更新 `验收状态` 仍正常通过）；`F5c` 评审者与 `T3 / T4.1–T4.3` 实现取证作者冲突或试图拉 `Claude` 替补脚本反作弊细节审，精确命中 `PL-REV-INDEP`；
  - **反例 F6a & F6b（锁卷哈希篡改与任务未勾完分别独立阻断 CP1）**：`F6a` 篡改 `accept_*.md` 第 2 章字符，精确命中 `PL-CP1-LOCK`；`F6b` 保留 `T1–T4` 任一 `[ ]` 未勾选，精确命中 `PL-CP1-TASKS`；
  - **反例 F7（未同构归档或全仓入链断链阻断 archive）**：活动目录残留文件，或 `docs/design/`、`docs/archive/changes/` 入链失效，精确命中 `PL-ARC-SYM`；
  - **反例 F8、F9 & F10（draft 阶段异常、擅自降级与跨仓/清单外/补等级后回退原文仍阻断）**：`F8` 复杂计划缺阶段或改名阻断 `draft`；`F9` 简单计划设 `>= 2` 个 `CP` 或未附降级批准擅自标简单精确命中 `PL-LVL-DOWNGRADE`；`F10` 跨仓计划、不在目标仓 `docs/plan/.legacy-plans.json` 内的计划、已标记 `migrated: true` 或 Git 历史曾提交过 `计划等级：` 后又把全文回退至初始版本的计划，均精确命中 `PL-LVL-REQ`；
  - **正例 P1–P4（全链路合规放行、D4-A 合法研发替补放行与确定性哈希/HO-CLAUDE-SCOPE 核验）**：`P1` 合规复杂计划依次通过 `draft / cp0 / cp1 / archive-preflight（保留唯一在途收尾任务 [ ]） / archive（100% [x]）`，且验证 `t1` 被 `T2` 演进取代、`T2` 默认仅含研发细节防漏审（零重复 Claude 调用）、当 `Codex` 为实现作者时由 `Gemini` 按 `D4-A` 合法替补细节审收据在 `cp0 / cp1` 正常放行，以及 `实跑取证中` 状态下重跑 `cp0` 均不自锁；`P2` `S0 / S1` 简单计划与正常勾选进度的未迁出冻结存量计划放行；`P3` `已废弃` 复杂计划同构归档放行；`P4` 验证 `classify`（输入互斥与否定语境免疫）、`init`（默认禁覆盖、禁符号链接穿越、禁重复元数据键）、`lock-hash`（必用本计划 `CP0` 实际锁卷哈希做字节级确定性回归断言），以及 `check_handoff.py` 对 20 个必填字段 + 2 个可选字段（含 `claude_arch_audit` 枚举、向 Claude 派发 `codex_detail` 或单个 `.py` 文件只读细节审精确命中 `HO-CLAUDE-SCOPE` 阻断、借次要文本字段拆分长文绕过 `800 / 1,000` 全字段字符总上限精确命中 `HO-CLAUDE-SCOPE` 阻断、目录类型绑定、未知字段拒绝与 `handoff.example.json` 零回归）的完整断言。
- **量化通过判据**：`>= 18` 组单测 100% 通过，`skipped == 0`，每条规则码均有独立单变量用例覆盖。

### 2.3 S3：四大核心目录契约、`test_verdantflare_markdown.py` 与存量零回归（归属 `T4.1`）

- **测试对象**：`docs/reviews/{AGENTS,README}.md`、`docs/acceptance/{AGENTS,README}.md`、`docs/archive/{README.md,reviews/,acceptance/}`、`verdantflare-meta-markdown/scripts/{check_md.py,test_verdantflare_markdown.py}` 及 `docs/plan/*.plan.md` 存量在途计划。
- **执行入口与命令**：
  - 运行 `python -X utf8 -m unittest discover -s verdantflare-skills/skills/verdantflare-meta-markdown/scripts -p "test_*.py"`（含新增 `rd-review`、`acceptance`、受控目录非法命名失败闭合及排除 `AGENTS.md / README.md` 用例）；
  - 运行 `check_md.py` 检查新增核心目录规范与索引、本计划 `reviews / acceptance` 实例及创作分镜 `*.review.md` 样本；
  - 对 `docs/plan/` 下全部在途计划运行 `plan_lint.py check --gate draft --repo-id verdantflarehub/verdantflare-design` 与 `check_md.py` 回归检查。
- **量化通过判据**：`test_verdantflare_markdown.py` 全绿通过（`skipped == 0`）；全部核心目录文档 `0 errors, 0 warnings`；冻结清单内历史在途计划零新增阻断。

### 2.4 S4：全局提示词 `<= 600B` 增量、双域 `< 8 KB`、`test_agents_sync.py` 与格式合规演练（归属 `T4.2`）

- **测试对象**：`agents/core/AGENTS.md`、`agents/core/rules/User.md`、`agents/README.md` 及 `agents/scripts/{_common.py,export_agents.py,import_agents.py,test_agents_sync.py}`。
- **执行入口与命令**：
  - 运行 `python -X utf8 -m unittest discover -s agents/scripts -p "test_*.py"`；
  - 实测仓库 `agents/core/{AGENTS.md,rules/User.md}` UTF-8 字节总和及相对 `5,657 Bytes` 基线的净增量，运行 `python -X utf8 agents/scripts/export_agents.py --verify-only`；
  - 在 `.tmp/rd-plan-governance/mock-home/` 构造隔离演练环境：① 对含未锚定本地修改的目标文件执行默认 `import_agents.py`（断言失败闭合拒绝覆盖并生成备份）；② 对含个人身份块的模拟本机目录执行受控导入后，实测 `mock-home/.agents/{AGENTS.md,rules/User.md}` 安装态 UTF-8 字节总和；③ 执行 `export_agents.py` 反向导出至临时仓库目录后，**对剥离个人块后的导出 Markdown 文件运行 `md_format.py --check` 与 `check_md.py`**，并验证异常锚点/敏感路径内存预扫零脏写与连续两次往返幂等。
- **量化通过判据**：`test_agents_sync.py` 全绿通过（`skipped == 0`）；**仓库双文件净增量严格 `<= 600 Bytes`，且仓库模板与导入后 `.agents/` 安装态双域 UTF-8 字节总和均严格 `< 8,192 Bytes`**；剥离个人块后的导出文件 `md_format.py --check` 与 `check_md.py` 均为 `0 errors, 0 warnings`；隐私扫描退出码 `0`。

### 2.5 S5：`T1 / T2` 原生多智能体评审收据对账与 `.gdrive` 配图逐项验真（归属 `T4.3`）

- **测试对象**：本专项已完成闭环的 `T1`、`T2` 评审报告（`docs/reviews/review_20261114_rd-plan-governance_{t1,t2,t2-r2}.md`）及专题设计嵌入的 3 张 ChatGPT Image 2.6 配图（`T4` 验真审计报告 `t4.md` 由 `T4.4` 独立产出并直接记入第 4 章第 ⑤ 维防伪锚点）。
- **执行入口与命令**：
  - 优先通过宿主调度接口（`claude_task_status / claude_task_result` 与 `codex_task_status / codex_task_result`）按 ID 回查 `t1.md`、`t2.md` 与 `t2-r2.md` 登记的原生 `session_id / thread_id`、`task_id`、`turn_id` 真实性及最新有效报告 `t2-r2.md` 的 `评审对象哈希`（若 MCP 内存句柄已被运行时清理，则比对本机持久化会话索引/账本文件记录及 `SHA-256` 兜底验真）；
  - 对 3 张已上图资产逐一执行 `gdrive.py verify --key <asset_key>`，并运行 `gdrive.py audit --doc docs/design/skills/details/K11-agent-and-meta-skills.plan-governance.md`。
- **量化通过判据**：`t1.md`、`t2.md` 与 `t2-r2.md` `评审结论` 均为 `已通过`、`未闭环 HIGH/MEDIUM：0`，最新有效报告 `t2-r2.md` 哈希与冻结切片一致且原生会话收据 100% 回查匹配；3 张配图 `verify --key` 全部 `valid: true`，`gdrive.py audit` 返回 `unmanaged: 0, missing: 0, compliant: true`。

### 2.6 S6：本机 `~/.agents/skills/` 双域哈希对账与安装态门禁分段验证（前半段归属 `T4.2`，后半段放行归属 `T5.1`）

- **测试对象**：本机 `%USERPROFILE%/.agents/skills/{verdantflare-meta-plan,verdantflare-meta-markdown,verdantflare-meta-agent}` 安装副本及安装态 `plan_lint.py` 门禁实况行为。
- **执行入口与命令**：
  - **前半段（`T4.2` 执行）**：运行三项技能 `scripts/install.py` 同步至本机全局技能目录，逐文件比对仓库源码与安装态受管文件集合及 `SHA-256` 摘要；直接调用安装态 `%USERPROFILE%/.agents/skills/verdantflare-meta-plan/scripts/plan_lint.py` 对本计划执行两步核验——① 运行 `--gate cp0` 断言退出码 `0`（验证处于 `T4` 期间不发生状态互斥）；② 在 `T4.4` 签字前运行 `--gate cp1`，断言退出码 `1`（精确命中 `{PL-CP1-TASKS, PL-CP1-ACC, PL-CP1-REV}`）拦截未完成且未验真放行；
  - **后半段（`T5.1` 入场执行）**：待 `T4.4` 完成双轨验真审计签署 `t4.md` 后，在 `T5.1` 呈报 `CP1` 前再次调用安装态 `plan_lint.py check docs/plan/2026.11.rd-plan-governance.plan.md --gate cp1`，断言退出码 `0` 全绿放行并记入第 5.1 节。
- **量化通过判据**：三项技能仓库源码与安装目录受管文件集合及 `SHA-256` 100% 一致；安装态 `plan_lint.py` 对 `T4.2` 的 `cp0` 放行、签字前 `cp1` 三码精确阻断及 `T5.1` 签字后 `cp1` 放行全部精确符合预期。

## 3. Evidence

Fixture only.

## 4. Verification

Fixture only.

## 5. Closeout

Fixture only.
