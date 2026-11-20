# 桌面安装器原生自举工程工具

## 1. 用途与边界

开发者使用固定输入装配资源并离线构建 macOS arm64／Windows amd64 原生启动器。启动器先核对完整随包资源，再直接启动随包隔离 Python；无需最终用户预装 Python。工程命令仅核对资源或在明确新目录运行合成事务自检，不能安装到用户宿主或报告五端就绪。

当前仍是未签名、不可公开安装的工程工具。Windows 仅支持交叉构建，私有 ACL 与原生执行未验证；GUI、可信发行、真实宿主活动／凭据与业务验收尚未交付；归档／接收数据校验器已通过有界独立审阅。完整范围见主工作区[原生自举设计](../../../../docs/design/skills/details/README.agent-compatibility.native-bootstrap.md)和[五端适配计划](../../../../docs/plan/2026.11.agent-skill-compatibility.plan.md)，本目录不另立产品契约。

## 2. 构建前置与接口

构建侧使用 Python 3.12、macOS arm64，以及已通过外部摘要校验的组合运行时候选、原始 TOMLKit 0.15.2 wheel 和官方 Go 1.26.11 SDK 归档。依赖锁为[安装器依赖](../host-config-dependencies.lock.json)与[Go SDK 来源](../workspace_tools/go-toolchain-lock.json)。工具无下载、全局依赖安装或开发缓存回退。

从技能仓库根运行以下导入命令可查看真实接口：

```text
python3 -B -c "import sys; sys.path.insert(0, 'src/desktop-skill-delivery/installer_tools'); import bootstrap_bundle; help(bootstrap_bundle.build)"
```

`bootstrap_bundle.assemble(candidate, expected_sha, wheel_path, output)` 只装配资源，不编译原生程序。`bootstrap_bundle.build(candidate, expected_sha, wheel_path, sdk_archive, output, work)` 另从固定原归档解包 SDK，并编译资源清单内的源码快照；输出根和工作根须均不存在且互不包含。输出保留源码摘要、原组件来源、TOMLKit 及 Go 许可。Windows 目标由输入候选确定，不能用 macOS 清单配一份 Windows EXE 声称完成 Windows 包。

`build` 在工作根写 `build-receipt.json`，包括清单／二进制摘要、工具链来源、源码摘要、是否原生运行及未签名／未验收标记。macOS 构建后实跑资源核对；Windows 构建不执行 EXE。工作根保存受控 SDK 和私有缓存，不进入 Git。

## 3. 原生工程命令

macOS 输出根中的实际命令：

```text
./vf-bootstrap verify
./vf-bootstrap self-check --output /explicit/new/engineering/output
```

第二条的绝对路径为通用示例，须替换为允许的新工程临时目录。已有目录、资源树内路径或链接父目录均拒绝；本工具不默认选择用户技能目录。`self-check` 只验证合成技能文件、JSON／TOML 配置应用、中断恢复、再次提交及备份清理。退出码 0 只证明这次工程自检，不证明桌面加载或 MCP 业务通过。

Windows 输出名为 `vf-bootstrap.exe`；当前 `self-check` 因私有存储未验证而拒绝，不能依据交叉编译把它登记为可用安装器。资源核对与自检均不执行网络请求、不读取账号凭据。

## 4. 源码与测试入口

- [bootstrap_bundle.py](bootstrap_bundle.py)：固定依赖装配、原始 SDK 校验与离线编译。
- [driver.py](driver.py)：固定隔离 Python 驱动和合成事务自检。
- [native/main.go](native/main.go)：平台／清单／资源验证与固定子进程边界。
- [native/main_test.go](native/main_test.go)：资源变更、别名／链接、JSON 重复键和退出码测试。

Go 测试使用受管 SDK，须关闭网络和自动工具链，并将模块／编译／临时缓存定位到主工作区 `.tmp/`。测试源默认在原源码目录运行；当前没有面向用户的测试入口。临时 Swift 可行性代码不随本工具交付，正式原生源码统一在 `native/`。

## 5. 工程包归档与接收

[bootstrap_transfer.py](bootstrap_transfer.py) 提供 `verify_folder(folder, manifest_sha, binary_sha)`、`archive(folder, manifest_sha, binary_sha, output)` 和 `receive(archive_path, archive_sha, manifest_sha, binary_sha, output=None)`。外部摘要必须从受控交接取得，不能从同包文本推定发布者可信身份。`receive` 在新输出根创建前完成所有成员预检；不提供输出根时仅数据校验，不执行任何资源代码。

归档保留固定成员顺序、时间和类型／模式，相同已核实输入可重复生成相同字节。已有输出、链接父目录、额外成员、错误清单／原生程序／资源摘要均拒绝。Windows 在其他系统接收只是数据完整性检查，不能当作 Windows 安装或 ACL 验收。详细边界见[工程包交接设计](../../../../docs/design/skills/details/README.agent-compatibility.native-bootstrap.transfer.md)。旧资源包内的构建源码快照原样保留，新交接工具不追溯纳入旧清单。

## 6. 原生离线安装事务

`offline-operation --request <file>` 接收有限工程请求，支持 `apply`、`uninstall`、`recover`、只读 `status` 及 `inventory`、`collect`、`recover-gc`、`migrate-binding`，复用同一三资源核心与运行时维护器。首次只能新建隔离作用域，接续必须匹配作用域 ID、格式和资源版本；不能接管已有用户目录。`apply` 接收外部摘要指定的完整 11 包工程候选，运行时来自已核实随包树，地址使用 `.invalid`，不读取凭据。`interrupt_after` 只供工程测试真实进程退出，公开流程不提供。

[offline_operation.py](offline_operation.py) 负责请求、包与作用域校验和事务接入；原始运行时清单、版本存储及生命周期维护器均随新资源纳管。在首次暂存前登记封闭工程作用域，后续暂存、安装、恢复和维护均遵守同一活动锁；`status` 保持只读。盘点返回版本、保护原因和快照摘要，回收必须指定版本及刚读取的快照，未完成回收沿原记录恢复。

新作用域采用 v2 描述，旧 v1 及普通命令中的不同资源包绑定均明确拒绝，保留原字节。跨包必须显式调用 `migrate-binding`，指定原包摘要、旧包实读的盘点快照及空故障注入点；有活动、在途事务、未就绪版本或资源漂移时拒绝。新包在全部资源锁内记录前后绑定和快照，再原子切换描述；重复调用要求原意图仍匹配，不能只靠新描述宣称迁移成功。迁移不更新运行时／技能／配置，后续 `apply` 才通过三资源协调应用新候选。数据夹具的不同版本应用、同版重装及无引用回收均不能当作真实新运行时升级证明；工程锁也不能当作真实五端进程发现证明。旧候选保持原字节，不追溯获得新命令。

源码构建后必须从实际原生入口验证空 PATH、11 包应用、真实进程退出／恢复、无变化重装、活动互斥及引用／快照保护；结果尚不能视为用户可安装或桌面可调用。字段与边界见[离线安装事务设计](../../../../docs/design/skills/details/README.agent-compatibility.native-bootstrap.offline-operation.md)，维护器的封闭范围见[生命周期设计](../../../../docs/design/skills/details/README.agent-compatibility.runtime-lifecycle.md)。

`uninstall` 另有且仅有 `interrupt_after` 字段，只接续完整登记的作用域，不初始化或补齐缺失目录。它通过原三资源补偿机制移除受管技能、VF 连接和运行时指针，保留用户新增文件、非 VF 配置、模式、封条与私有恢复记录。版本树留给按新快照执行的独立回收；移除未提交时原版本备份继续保护恢复。重复移除和原发行重装均通过原所有权校验，不递归清空技能根或作用域。完整用户卸载仍需五端适配、凭据清理、Windows 和正式发行验收，工程收据明确 `uninstall_acceptance=false`。

WorkBuddy 使用同一命令的 `vf.bootstrap-offline-operation/v2` 请求，增加固定 `host_profile=workbuddy`，仅接受 JSON。新作用域内采用 `workbuddy/mcp.json` 与 `workbuddy/skills/`，技能备份位于独立 `state/`；请求不能传入 URL、凭据或真实宿主目录。v1 与该 profile 的作用域不能互相恢复或静默迁移，旧通用 JSON／TOML 绑定保持。即使字节提交成功，收据也保留 `host_refresh=refresh_required` 和 `host_verified=false`。原生全链验证入口为 [probe_workbuddy_bootstrap.py](../validation/probe_workbuddy_bootstrap.py)，使用两份外部摘要指定的不同发行候选，验证空 PATH、实际进程退出恢复、更新、卸载及用户内容保留；不启动厂商程序，不证明真实桌面或业务通过。字段与边界见[WorkBuddy 入口设计](../../../../docs/design/skills/details/README.agent-compatibility.native-bootstrap.offline-operation.md#81-原生离线入口衔接)。
