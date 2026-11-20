# 桌面技能受管运行时工程工具

## 1. 当前依据

- [统一运行时设计](../../../../docs/design/skills/details/README.agent-compatibility.integration.md#2111-python媒体与-workspace-的统一工程运行时)：组件组合、来源保存、接收与隔离验证。
- [可重定位运行时设计](../../../../docs/design/skills/details/README.agent-compatibility.integration.md#218-可重定位运行时构建)：原 Python／wheel 字节、相对入口、树外缓存与来源变换。
- [共享 CLI 工具](../workspace_tools/README.md)：源码、模块 h1、许可证及独立构建。
- [运行时生命周期设计](../../../../docs/design/skills/details/README.agent-compatibility.runtime-lifecycle.md)：隔离作用域引用、子进程活动与可恢复回收。

## 2. 工具与范围

工程工具使用 Python 3.12 标准库。当前真实构建与原生探针证据来自 macOS arm64；Windows 的材料装配、归档接收和 PE 检查不等于 Windows 执行。所有候选均未签名、不可公开安装，不是面向用户的一键启动器，也不连接桌面、MCP 或媒体生成服务。

- `runtime_bundle.py`：原始解释器／wheel 材料闭包。
- `portable_runtime.py`：原 Python 文件与 vendor wheel 树、包命令及相对入口。
- `runtime_store.py`：POSIX 私有版本树暂存、复核与激活；本身不决定发行可信或可回收版本。
- `runtime_composition.py`：组合 Python、媒体与 workspace；归档、接收及还原原始组件。
- `../validation/probe_composed_install.py`：新私有目录中的实际安装／中断恢复／迁移与 helper 检查。
- `runtime_lifecycle.py`：新隔离 POSIX 作用域的引用盘点、继承活动锁和版本回收；拒绝接管既有用户根。

构建和接收显式传入外部 SHA-256。摘要须来自可信交接记录；读取待验材料自行计算其摘要不能建立发布者信任。工具拒绝缺失输入、未知平台、路径别名、链接输入、损坏字节和重复／未列文件，不从网络或开发缓存补齐材料。

## 3. 组合与校验

三个输入分别为已核验的 portable Python ZIP、媒体候选目录及其独立 `component-integrity.json`、workspace 候选目录及其 `workspace-runtime.json`。示例中的路径和摘要占位符须由调用者替换为实际交接值，各输出目录必须不存在，父目录必须已存在：

```sh
python3.12 runtime_composition.py assemble \
  --python-archive INPUT/python.zip --python-sha256 PYTHON_ARCHIVE_SHA256 \
  --media-folder INPUT/media --media-integrity INPUT/component-integrity.json \
  --media-sha256 MEDIA_MANIFEST_SHA256 \
  --workspace-folder INPUT/workspace --workspace-sha256 WORKSPACE_DESCRIPTOR_SHA256 \
  --release desktop-runtime.engineering.1 --output OUTPUT/candidate
python3.12 runtime_composition.py verify-folder \
  --folder OUTPUT/candidate --sha256 COMPOSED_DESCRIPTOR_SHA256
python3.12 runtime_composition.py archive \
  --folder OUTPUT/candidate --sha256 COMPOSED_DESCRIPTOR_SHA256 \
  --output OUTPUT/composed.zip
```

`assemble` 首先完整校验三个原组件，构建不执行包代码。Python 原条目保持，平台入口的 scoped-bin 变换记录原／新摘要，原入口字节另存。源与许可保留于 `components/`：workspace 原始路径只出现在原描述文件，实际材料文件名为内容摘要；`@v` 等路径不会进入运行时路径策略。只把所选平台的三个工具放在顶层 `bin`。

`composed-runtime.json` 绑定 `composition-lock.json`、`tree-manifest.json` 和 `provenance.json`；锁记录原输入摘要、平台、版本和组合／入口工具源码摘要。对象目录按内容摘要去重。接收器使用对应版本工具验证确定的投影，不执行候选内保存的工具源码；工具版本不匹配时须使用该候选对应的受管校验工具，不静默放宽。

## 4. 接收与还原原组件

```sh
python3.12 runtime_composition.py receive \
  --archive OUTPUT/composed.zip --sha256 COMPOSED_ARCHIVE_SHA256 \
  --output OUTPUT/received
python3.12 runtime_composition.py restore \
  --folder OUTPUT/received --sha256 COMPOSED_DESCRIPTOR_SHA256 \
  --output OUTPUT/restored
```

归档接收在写入前核对完整文件集、对象长度／摘要、入口、平台格式、原组件元数据及确定的逐条来源投影。未经单独验证，组合锁的原输入摘要仍是来源记录，不能由“读到该值”推定已重新下载或验过上游原件。

`restore` 在新私有目录还原 workspace 的全部原路径／模式，并使用原 `verify_runtime` 校验器重新核对源码、模块 h1 和许可；媒体全部原文件按原清单复核。Python 还原原树与元数据，再使用原确定性归档器重建原 ZIP，要求与原输入 ZIP 摘要相同，从而证明该构建工具链下的完整往返，不仅是部分文件相等。该 ZIP 字节复现受原归档工具链约束，不能把另一压缩库版本的结果差异隐瞒为相同。

还原材料是工程取证，不修改已安装版本树。正式签名、许可证授予、安装准入、Windows ACL、原生启动器和五端验收分别留证；源代码材料存在不代表已取得主模块公开分发授权。

## 5. 隔离安装与原生验证

```sh
python3.12 ../validation/probe_composed_install.py \
  --candidate OUTPUT/received --sha256 COMPOSED_DESCRIPTOR_SHA256 \
  --sample INPUT/vf-hub-skill-example-0.1.0.zip --sample-sha256 HUB_SAMPLE_SHA256 \
  --output OUTPUT/native-probe
```

探针仅支持实际 macOS arm64，在新私有目录暂存整个运行时，移动含空格父目录，再经既有三资源事务安装示例中 11 包。配置写入后模拟中断并验证三资源回到原状态，再完整提交。使用初始空 PATH 启动实际解释器，检查三个受管工具、缺身份拒绝、已安装 Music helper 及六项合成媒体功能，最后复核完整树、原候选、其他配置和清理终态。固定测试 URL 为不可达测试域，不执行真实 MCP 调用。

成功记录保存在 `probe-receipt.json`，日志脱敏。该结果证明隔离工程集成，不能填作十个真实桌面环境、110 个技能单元或 60 个业务场景的通过证据。

## 6. 生命周期维护工程

`RuntimeLifecycle(store, scopes)` 接受同一 `runtime_tools.runtime_store.RuntimeStore` 和固定的三资源 `InstallOperation` 集合，首次 `initialize()` 要求运行时和操作记录为空。登记根绑定、作用域集合与初始配置指纹后，使用维护器的 `stage`、`apply`、`recover_operation`；禁止以原始引擎绕过活动锁。已有用户配置、未登记进程及 Windows 不属于该隔离调用约定。

`inventory()` 读回当前安装目标、实际配置／技能、激活指针及备份。`run(digest, executable, arguments)` 只启动已引用树内声明为可执行的普通文件，并让子进程继承共享活动描述符；命令完成或父进程退出前，维护排他锁不能取得。`lease()` 供需要自行管理子进程的工程调用者使用，其描述符必须显式经 `pass_fds` 传给整个运行活动。

`collect(digest, expected_revision=...)` 只回收刚盘点确认就绪且无引用的树。所有未清理操作／备份、未知孤儿、字节／模式／配置漂移、活动锁冲突和陈旧快照均拒绝；不按年龄自动删除。回收先持久化不可变记录和小型进度，再同卷移入隔离区，按原清单逐项校验删除；中断只用 `recover_gc()` 继续。未知文件保留，不提供强制递归删除选项。

```sh
python3.12 ../validation/probe_runtime_lifecycle.py \
  --candidate OUTPUT/received --sha256 COMPOSED_DESCRIPTOR_SHA256 \
  --sample INPUT/vf-hub-skill-example-0.1.0.zip --sample-sha256 HUB_SAMPLE_SHA256 \
  --output OUTPUT/lifecycle-probe
```

完整树探针安装实际 11 包，更新夹具仅修改工程发行标签、保留全部运行时字节，核对引用／活动拒绝、旧版本回收中断恢复和更新树实际运行。它不生成第二份正式组合发行。独立小树回归位于相邻 `lifecycle_tests/`，包括多作用域旧引用、首次安装前配置漂移、真实父进程退出后的继承锁和真实回收进程强退恢复；不要把其数量与 `runtime_tests/` 的组件测试相加冒称桌面验收。
