# 桌面技能验收准备入口

## 1. 当前依据

- [验收入口设计](../../../../docs/design/skills/details/README.agent-compatibility-acceptance.md#71-验收入口与冻结前准备)：矩阵、作用域、证据和退出行为。
- [完整考卷](../../../../docs/acceptance/accept_20261115_agent-skill-compatibility.md)：110 个技能单元、60 条业务行和全部断言。

## 2. 使用范围

`run.py` 当前只执行 `mode=preflight`：创建或验证完整矩阵、盘点指定作用域并核验证据清单。它不启动应用、联网、读取系统凭据或写宿主配置，不执行任何业务测试。正式桌面驱动、锁卷／CP0 准入与独立验真尚未接入，所有本轮准备对象保持 blocked，未选对象为 not_run；不会输出正式 pass 或验收退出码 0。

默认准备对象为 macOS WorkBuddy 的 11／6；完整矩阵仍为五端双系统的 110／60。Claude 桌面暂停，Windows 平台工作归独立计划，原行全部保留。逻辑环境名不代表已绑定实际设备，null 字段不填示例身份或任务 ID。

## 3. 工程使用

从设计主仓库根目录执行，先建立任务专用临时目录。下面路径均为相对工作区的工程示例，输出文件或目录必须尚不存在：

```text
python3.11 -B verdantflare-skills/src/desktop-skill-delivery/validation/run.py --init-matrix .tmp/agent-skill-acceptance/matrix.json --exam docs/acceptance/accept_20261115_agent-skill-compatibility.md
python3.11 -B verdantflare-skills/src/desktop-skill-delivery/validation/run.py --matrix .tmp/agent-skill-acceptance/matrix.json --exam docs/acceptance/accept_20261115_agent-skill-compatibility.md --phase candidate --scope-os macos --scope-hosts workbuddy --output .tmp/agent-skill-acceptance/results
```

两条命令预期均退出 2，表示尚未实跑，不代表正式验收成功。初始化返回 `matrix_prepared`；准备报告返回 `blocked`，列出尚缺的驱动和治理证据。输入损坏、考卷摘要改变、范围错误或证据不一致退出 1，并输出固定错误码。`--help` 仅展示参数，不执行验收。

`--select` 接受考卷命令中的组名，例如 `host,build,package,install,route` 或 `journey,onboarding,compatibility,recovery,docs,operations`；未知、空值或重复组名拒绝。`--scope-hosts codex,antigravity,workbuddy,deepseek` 可盘点当前 Mac 四端 44／24，不缩减完整矩阵。选择 Claude 不会解除用户暂停。

## 4. 证据与输出

`--evidence-manifest` 可指定只读证据清单，Schema 为 `vf.desktop-skill-validation.evidence/v1`，每个文件项只有 `path`、`bytes`、`sha256`。path 相对于清单父目录；路径越界、链接、硬链接、重复／大小写别名、字节数或 SHA-256 不匹配均拒绝。文件内容流式核验，不载入媒体全文，也不执行证据内的命令。 JSON 结构输入最大 4 MiB、最多 64 层容器嵌套；字符串内括号和转义引号不计层数，超限统一返回 `JSON_NESTING_LIMIT`，不依赖 Python 版本的递归上限。

输出私有目录中的 `summary.json`、`results.jsonl` 由 `manifest.json` 固定字节数和 SHA-256；已有目录拒绝覆盖。结果的未知字段为 null，并列入 `unobserved_fields`。输出清单也可作为下一轮的完整性输入，但其成功只证明字节未变；不能证明桌面来源、业务断言或独立验真，文件内的 pass 字符串不会被采纳。

当前只验证 POSIX 文件操作；没有 Windows 运行结论，也不是面向 VF 用户的安装产品。正式证据存储及支持发布仍沿主设计，不把临时目录改称受管存储。

JSON 整数显式限制为最多 4300 位十进制数字，负号不计；转换前以 `JSON_INTEGER_LIMIT` 拒绝超限输入，保持 macOS 系统 Python 与受管新版本的拒绝行为一致，不修改解释器全局安全设置。

## 5. 行为检查

```text
python3.11 -B -m unittest discover -s verdantflare-skills/src/desktop-skill-delivery/validation/tests -v
```

测试使用显式合成考卷和证据，覆盖矩阵覆盖、选择集、伪造状态、输入漂移、证据篡改、文件安全、隐私错误码和不可覆盖输出。测试通过仅证明准备工具行为，不能增加正式桌面或业务通过数。

## 6. 已运行 WorkBuddy 的只读观测

`probe_workbuddy_desktop.py` 适用于已安装且正在运行的固定 macOS WorkBuddy 5.11.3 发行。它不启动桌面，核对原厂存活响应、监听进程／启动时间、应用指纹和 Apple／厂商签名；只访问固定回环候选与原厂路径，无代理、重定向、账号或用户配置访问。详细边界见[宿主设计](../../../../docs/design/skills/details/README.agent-compatibility.host-profiles.md#3414-已运行桌面的只读存活观测)。

```text
python3 -B verdantflare-skills/src/desktop-skill-delivery/validation/probe_workbuddy_desktop.py --output .tmp/agent-skill-acceptance/workbuddy-desktop.json
```

输出父目录须已存在且路径不经软链接，文件须尚不存在；可用 `--app` 指定实际应用目录，不能传入网络地址。成功退出 0 只表示取得运行观测，仍为 `host_verified=false`、`process_attested=false`、`desktop_acceptance=false`、`mcp_acceptance=false`；失败退出 1，不输出原始系统错误或宿主响应。收据不包含本机路径、进程号或监听端口，不能拿存活响应证明登录、技能加载、授权或业务通过。
