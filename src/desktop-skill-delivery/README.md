# 桌面技能工程交接工具

## 1. 用途与边界

供 VF 技能交付方与 Hub 开发者核对真实包字节、目录解析和依赖关系。这是未完成的工程候选，不是面向 VF 用户的一键安装工具。校验命令无需网络、私有仓库、Studio 账号或 Token，不执行技能脚本、不修改宿主配置。

当前装配 Artifact、Project、Character-face、Reference-learning、Video、Image、Music-MV、Music、Speech、Short-drama 与 Blender 十一个包。候选始终为 `installable=false`、`public_distribution=false`、`authentication=unsigned`；十个桌面／系统支持行全部 `not_run`。不要将候选登记为客户可下载的 `publishedReleaseId`。

`assembled` 只代表字节完整。另有工程运行时候选包含 numpy、scipy、soundfile、librosa、Pillow、jsonschema／referencing、FFmpeg／FFprobe 和 Project 工作副本 CLI；这些不在本技能 ZIP 内，须按来源锁组合及验证，不能依赖开发机全局安装。Music 的批准外部 URL 导入仍需可用上传链路。完整用户安装器、五端 profiles、用户指南及真实授权／运行依赖交付仍未完成，不能在用户机器上解压后冒称安装完成。

构建器 0.2.8 起，每包增加标准 `skill.json`，包含稳定名称及与清单一致的发行版本，供 WorkBuddy 原生版本读取。该文件确定性生成并纳入来源锁和逐文件摘要；接收校验拒绝描述与发行版本错配。原 SKILL.md 指令保持既有投影字节，不生成厂商私有市场元数据。旧候选使用其随包旧工具验证，不原位追加描述文件。

## 2. Hub 接收方校验

将交接 ZIP 解压到全新的工程临时目录。开发者环境使用 Python 3.10+，从解压根目录运行：

```text
python3 tooling/delivery.py verify candidate
```

Windows 开发者可使用已安装的 `py -3` 替代 `python3`。这是工程接收方环境要求，不是最终 VF 用户的安装前置条件。

成功返回 JSON 的 `status=integrity_verified`，退出码为 0；拒绝返回 `status=rejected`，退出码为 2。成功仅证明清单、来源锁及包字节相符，不证明发布者身份或桌面功能。随包的工具也未签名，接收来源仍须按团队现有协作方式确认；不能用同包的 SHA 文件建立信任根。

- `candidate/release-manifest.json`：11 个稳定 ID、真实包位置与摘要、依赖、缺项及支持状态。
- `candidate/source-lock.json`：来源提交、工作树修改标记、完整工具集摘要、构建 Python 版本、逐文件原始来源及变换输出摘要。
- `candidate/packages/`：已装配技能的 ZIP；缺项不生成空 ZIP。
- `candidate/manifests/`：每包文件及来源对应关系。
- `candidate/SHA256SUMS`：候选文件完整性校验值。
- `tooling/handoff.schema.json`：供 Hub 解析候选清单的 Draft 2020-12 Schema；不代替包字节校验。

Video 包使用显式公开文档与纯 MCP 客户端投影，后台维护脚本不进入包。Image／Music-MV 的共享说明进入包内，兄弟技能引用指向发行集合内锁定依赖；Image 的未开放 MOSS 参数草案不进入公开调用指南，保留当前动态声明的能力边界。`source-lock.json` 的 `files` 锁定原始输入，`outputs` 记录输出路径、来源摘要、转换标识和输出摘要；复制文件两者相同，投影文件分别记录。`builder_sha256` 是排序后的完整工具文件摘要映射的摘要，不是单个 Python 文件的摘要。构建必须复用同一 CPython 小版本；接收方仅验字节，可使用其他受支持 Python 版本。旧 v1 候选继续使用其随包旧工具校验，不由 v2 冒认。

Music 包将 24 份中文审核参考映射为明确 ASCII 别名；来源锁保留原始 Unicode 路径和摘要，归档路径继续只允许 ASCII。VoiceModel 说明与 Schema 按原固定提交快照携带，`references/source-provenance.json` 记录出处，公开说明不要求读取私有仓库。不含内部技能研发说明或无调用依赖的环境加载器；科学计算脚本保持源字节，尚未交付其运行时。

Speech 公开包包含标准参考声线配置工具，接受当前 `engines` 能力数组并保留旧平面记录兼容。后台声谱融合实验脚本不分发，当前没有公开融合 MCP；输入整理和已有候选复用可继续，新融合生成保持未完成。音频转换与受管 Python 未交付，不能从 helper 离线测试推断完整 Speech 可用。

Short-drama 包含编剧、导演、审核与制作资料、六个脚本及准备 Schema。私有固定引用已核对差异并迁移到同发行集合。Blender 是其显式锁定依赖，片场导出／校验工具随 Blender 包装配，真实实例与网页机位验收仍未完成；Ollama 拆解只消费已有本机模型，Pillow／jsonschema 运行时未交付，不能把文字资料已装配当作完整短剧制作可用。

Blender 包携带固定脚本准备器、通用导出器、校验器原字节快照与截图测量器；脚本经固定 Project 修订交实例执行，本地不安装 `bpy`。公开投影移除团队案例路径，保留来源摘要与同发行集合依赖。文件闭包不证明真实实例、网页截图来源、安装器或运行时已经可用。

Schema 名为 `vf.skills.handoff/v2`，仅供工程对接，不预先冻结正式发行接口。Hub 可联调解析、展示缺项和拒绝安装；正式签名发行、授权下载与用户安装仍按双方产品设计执行。

## 3. 交付方构建

从技能仓库根目录执行，`OUTPUT` 替换成工作区允许的现有临时父目录，各输出文件／子目录必须不存在：

```text
python3 src/desktop-skill-delivery/delivery.py lock --source . --release 0.2.0-integration.6 --output OUTPUT/source-lock.json
python3 src/desktop-skill-delivery/delivery.py build --source . --lock OUTPUT/source-lock.json --output OUTPUT/candidate
python3 src/desktop-skill-delivery/delivery.py handoff --bundle OUTPUT/candidate --output OUTPUT/vf-skills-0.2.0-integration.6.zip
```

构建采用逐文件允许清单，不递归发布整个技能目录。锁定后源码、构建器或仓库状态变化会拒绝构建，应查明差异后生成新来源锁。已交接版本保留原字节，更新使用新 release_id，不覆盖旧 ZIP。

同一来源锁和工具重复构建应逐字节一致；`handoff` 携带对应校验器、Schema 和本说明，不需要接收方访问构建仓库。构建和校验成功不等于已上传，入库与跨机读取需另有真实回执。

## 4. 本地验证

从技能仓库根目录运行：

```text
python3 -m unittest discover -s src/desktop-skill-delivery/tests -p test_delivery.py -v
```

这里的合成测试覆盖损坏输入、路径逃逸、漂移、不可变输出和虚假支持声明。它不占用五端双系统业务验收行，也不替代真正的安装、更新、回滚或 MCP 业务测试。

## 5. 安装器配置工程验证

`host_config.py` 为 JSON 内存方案，`host_toml.py` 为 Codex TOML 内存方案；两者均只维护自己的 VF 节点，不发现或写入真实宿主。`config_transaction.ConfigFile` 与 `toml_transaction.TomlConfigFile` 在显式隔离根提供 POSIX 文件事务；TOML 使用独立 schema，共用目标锁，拒绝把已有 JSON 记录按 TOML 恢复。

`InstallOperation` 默认仍使用 JSON；显式 `config_format='toml'` 选择 TOML 并绑定格式，禁止换格式恢复跨资源操作。Windows ACL、真实宿主活动／版本检查、秘密存储、配置刷新与正式发行准入尚须由完整安装器实现，不能直接对用户配置调用工程核心。

TOML 编辑器依赖固定 `host-config-requirements.txt` 和 `host-config-dependencies.lock.json` 的 TOMLKit 0.15.2。先以项目批准来源取得原 wheel、核对摘要和许可，再在隔离目标目录执行 `pip --no-index --no-deps --require-hashes --target ... -r ...`；不得以开发机全局包或随意安装最新版满足交付闭包。该依赖属于 D03 安装器，不修改已交接的业务运行时归档。

在已准备固定依赖的验证环境，从技能仓库根运行：

```text
python3 -B -m unittest discover -s src/desktop-skill-delivery/toml_tests -v
python3 -B -m unittest discover -s src/desktop-skill-delivery/transaction_tests -v
python3 -B -m unittest discover -s src/desktop-skill-delivery/lifecycle_tests -v
```

TOML 测试覆盖内存排版、真实配置备份、全部持久中断边界、外部编辑、所有权校验、三资源恢复和实际进程退出。`validation/probe_composed_install.py --config-format toml` 可另接收明确摘要的真实组合候选和 Hub 示例，在新隔离目录验证 11 包安装／回退及实际工具运行；默认 JSON 行为保留。所有证据均为工程验证，不占五端双系统桌面验收行。

WorkBuddy 的 [host_workbuddy.py](host_workbuddy.py) 消费由上层真实准入的精确连接 URL，输出原生默认 http 配置及固定毫秒超时。它拒绝静态凭据、额外执行字段、旧管理入口与连接替换；自身不认证来源或获取身份。显式隔离 `ConfigFile` 复用 JSON 所有权／CAS／恢复事务，自举资源包含该模块；当前离线命令仍为通用工程入口，不据此开放真实用户根安装。

原 JSON 事务的私有备份保留整份配置原字节，可能含其他服务的既有秘密；按原私有权限与清理机制处理，只输出摘要，不将备份加入交付包、Git 或普通证据。适配器不新增 VF Token，也不访问 WorkBuddy 私有 OAuth 凭据库。

既有 `InstallOperation` 支持显式 `config_profile='workbuddy', admitted_url=...`，仅允许 JSON 和 `mcp.json`。它在激活运行时／写技能前验证目标配置，并将 profile 摘要绑定到操作；通用 JSON／TOML 不能恢复其记录，默认 generic 的旧绑定保持不变。该精确类型可直接接入 `RuntimeLifecycle` 的 scope、活动租约与卸载，不另建协调器。URL 拒绝 WHATWG 会改写的数字 IP 别名及显式 HTTPS 默认端口；非默认端口保留逐字节绑定。

```text
python3 -B -m unittest discover -s src/desktop-skill-delivery/workbuddy_tests -v
```

该套件检查内存方案、实际隔离文件、跨进程中断及补偿。固定原厂解析器检查须显式提供 `VF_WORKBUDDY_CONNECTOR_SOURCE`，指向已取证 `5.11.3.43806934-ecace0f7` 的 connector 格式化源码，摘要 `06756026779070ba66d04233a5507085cbb710689b34f8962b138393214dbe71`；未提供时明确跳过，摘要不符拒绝执行。该文件的来源链在主仓库 T2.1 取证记录，不能以任意同名文件代替，也不将解析器消费写成真实桌面加载、OAuth 或业务通过。

[WorkBuddy 整链验证入口](validation/probe_workbuddy_install.py) 消费显式摘要的实际组合运行时、11 包交接候选和固定原厂源码，在全新隔离目录验证三资源中断恢复、安装、配置原厂解析、实际受管 Python／Project CLI／Music helper 执行及卸载原字节恢复。运行 `--help` 查看必填参数；运行时与样例均按外部给定摘要核验，不下载、不发现真实用户根。使用 `.invalid` 连接夹具、进程内 `config` 检查点中断，不能作为真实来源准入、宿主／模型调用或跨进程崩溃的证据；后者由专项进程退出测试单独覆盖。

探针使用 `workbuddy/mcp.json` 与 `workbuddy/skills/` 正常相对布局。协调器仅在 WorkBuddy profile 下允许这一精确根关系；其余重叠及 generic profile 的原限制保持，技能备份仍在独立状态根。布局契约及验证要求见[安装事务设计第 8 章](../../../docs/design/skills/details/README.agent-compatibility.native-bootstrap.offline-operation.md#8-workbuddy-原生目录布局)。

## 6. DeepSeek 来源事务工程入口

DeepSeek 固定发行的原生编辑与持久来源契约以[主仓库设计](../../../docs/design/skills/details/README.agent-compatibility.host-profiles.deepseek.md)为准。[host_deepseek.py](host_deepseek.py) 只规划原生局部 mutation 与读回，[host_deepseek_origin.py](host_deepseek_origin.py) 区分原用户覆盖和安装物化，[host_deepseek_journal.py](host_deepseek_journal.py) 在显式隔离目录持久化来源和待决操作。这些模块已有隔离原生三资源与生命周期协调，并进入自举源码闭包；实际桌面授权／凭据通道及原生运行命令接入仍未完成，已上传 Hub 示例也不提供用户安装入口。

适配器须先认证观测来源，取得 `snapshot()` 的账本 CAS，再调用 `prepare()`；只有待决记录同步完成后，才能取返回方案并通过宿主原生 revision 保护提交。成功响应后重新观测，调用 `resolve()` 核实实际结果并提交来源。进程中断后从同一账本恢复待决 ID／CAS，重新观测后完成或在写前状态完全不变时取消；不得重放旧 mutation。记录存在但后台 generation 改变时拒绝接管，不能删除账本重新安装来绕过。日志与诊断只使用常量错误码和非秘密回执。

账本仅保存身份和摘要，以 POSIX 私有目录／文件与非阻塞锁保护；Windows 路径明确未支持。配置精确层恢复、原始文件字节／模式、原生凭据解析、真实桌面加载和 MCP 业务成功分别验证，不能由账本落盘推断。测试要求明确提供已核对的固定 `VF_DEEPSEEK_SETTINGS_SOURCE` 与 `VF_DEEPSEEK_NATIVE_MODULES`，从技能仓库根运行：

```text
python3.11 -B -m unittest discover -s src/desktop-skill-delivery/deepseek_tests -v
```

原生模块探针使用 macOS 禁网沙盒及 Node 文件权限隔离；Python 测试的进程退出恢复不等同硬件断电验证。固定厂商依赖缺失时测试失败，不跳过后报告桌面通过。

[官方管理链探针](validation/probe_deepseek_management.mjs) 在自有 profile 中加载同一发行的 Connection、生成 descriptor、Gateway、SettingsController、LocalCredentialProvider 及原生配置编辑链。使用官方 launch-token 交换和 cookie admission，不直接创建 owner Peer；不启用网络监听和桌面。覆盖授权拒绝、配置增改删／独立读回、过期 revision、凭据写入／解析／撤销、其他凭据保留和 Connection 替换后旧 Peer 失效。`deepseek_tests/test_management.py` 另外把实际管理读回链交给持久来源规则验证，并检查来源篡改和缺文件写权限拒绝；该账本检查是观测链重放，尚未将账本写前事务与原生调用组成完整安装操作。

探针的内存 HTTP carrier 和自有 profile 授权不能证明安装器能取得真实桌面 launch-token、真实 HTTP／Electron IPC 可达、后台重启身份连续性、活动检测或三资源协调。实际 provider 只解析本次随机合成凭据；不读取用户环境／凭据，不运行 MCP Manager，不增加真实桌面验收通过数。

[host_deepseek_operation.py](host_deepseek_operation.py) 在整个原生操作／恢复链持有私有作用域执行锁，先由来源账本持久准备，再重新观测并验证同一方案，提交一次局部修改，最后独立读回提交来源。传输失败、写后失联或漂移均保留 pending；`recover()` 只观测和完成／取消原句柄，不调用 mutation。原生 transport 由真实适配器认证并绑定身份，此模块不能为调用方的输入认证，也没有实现桌面发现、活动准入或凭据与三资源协调。

`deepseek_tests/test_operation.py` 通过[私有测试驱动](validation/drive_deepseek_management.mjs)保留同一自有原生后台，在独立 Python 协调进程退出后恢复；管道只用于隔离验证，不提供产品管理入口。旧管理探针的常规调用仍执行原 9 场景；新的受控导出只复用其固定来源校验和官方模块初始化。测试分别验证实际原生调用前 pending 已存在、响应丢失／坏读回保护、执行与恢复锁竞争、外部修改保留，以及安装／更新／移除在四边界真实进程退出后的零重放。保持真实桌面、Electron 后台重启和硬件断电未验证边界。

上层安装器调用前须先同步保存随机 `request_id`、来源账本预检 CAS、目标连接摘要（删除为 null）及资源根绑定，再向 `NativeOperation.apply(..., request_id=...)` 传入同一请求 ID。原生 operation ID 由这些摘要和 scope 派生，保持既有磁盘 schema；若子操作已准备或完成但返回句柄丢失，可调用 `recover_request(expected_state, request_id, target_sha256, cancel=...)` 重建原句柄并只读恢复。不能直接采用账本里任意最新 pending。未开始或无变化请求不会写账本，恢复返回 `native_request_not_prepared` 只证明账本没有该请求，明确不证明原生配置仍相同。

`deepseek_tests/test_request_binding.py` 以预先同步的测试父意图，覆盖安装／更新／移除各在父意图、来源预写、写前重检、原生提交及来源提交五边界的真实协调进程退出；恢复输入不读取丢失的子操作句柄。测试同时保留原后台、验证恢复零写入、错误目标／CAS／请求 ID 拒绝、较新请求保护和旧 schema／无绑定句柄兼容。这是三资源协调所需的请求关联层，完整安装协调器及凭据生命周期仍待接通。

## 7. 原生管理三资源工程入口

[native_install_operation.py](native_install_operation.py) 的 `NativeInstallOperation(state_root, skill_root, runtime_root, native)` 将已暂存运行时树、技能文件及 DeepSeek 原生请求编为同一个隔离工程操作。`native` 必须为同一状态根的 `NativeOperation`；三个根独立、不重叠，调用方负责真实 transport 认证、活动准入及生命周期登记。日志只含根／身份摘要、文件账本和原生请求关联，保留原用户配置／凭据在原生通道内；该工程入口不接收 YAML 路径。

调用 `preflight()` 取得四个 CAS／观测字段，再把原样结果交给 `apply(release, payload, desired, runtime_digest=..., **preflight)` 或 `uninstall(**preflight)`。父记录同步后按运行时、技能、原生顺序处理；全程持有父锁和原生执行锁，原生提交时同时锁定并复核两个本地目标。`NativeOperation` 的内部持锁入口只允许持锁的同一线程调用，公共入口保持自取锁；父预检观测在写来源 pending 前再次核对。

执行异常保留父记录、待决来源及备份。另一次 `recover()` 从父记录恢复关联，已确认原生目标时核实本地子事务并完成，原生尚未准备且完整观测未变时恢复本地前态；原生待决仍为前态时默认拒绝并保留现场。`recover(cancel_native=True)` 是调用方明确选择原生取消，仍要求既有完整前态判据以及实际 transport 的在途请求／活动约束，不能拿测试管道代替真实准入。恢复永不发送原生 mutation；父全局提交后只核对并清理，不能盲目回滚配置。

`deepseek_tests/test_native_install.py` 使用可执行的小型本地运行时夹具和同一固定官方隔离管理后台验证安装／更新／移除、真实协调进程退出及保护条件。运行时夹具不等于产品运行时、真实 11 包安装或真实桌面；原生自举、生命周期与回收、凭据和双系统接入继续按主计划完成。模块收据保留完整安装器、桌面及 MCP 验收未通过标记。

[真实组合接收探针](validation/probe_native_install.py) 另从外部给定摘要的组合运行时及 Hub 示例校验全部输入，保留原 ZIP 模式、依赖与逐文件摘要，通过同一协调器安装实际 11 包，在原生提交后模拟 Python 异常，再经父记录读回完成、无变化重装、移除及原版重新安装。它在初始空 PATH 与禁网命令沙箱中执行实际 Python、FFmpeg、FFprobe、Studio Workspace 和已安装 Music helper，并保留非受管文件。参数为 `--candidate`、`--sha256`、`--sample`、`--sample-sha256`、`--modules`、`--output`，输出必须是根 `.tmp/` 内的新目录。该探针的异常恢复不是实际进程退出；实际退出由前述独立测试举证。真实运行时、原生模块隔离及桌面验收三个层级分开记录。

## 8. 原生作用域生命周期工程入口

[runtime_tools/runtime_lifecycle.py](runtime_tools/runtime_lifecycle.py) 显式接受原文件配置协调器及 `NativeInstallOperation`；[原生盘点模块](runtime_tools/native_lifecycle.py) 在持父、原生执行、来源账本和资源锁时核对来源及完整原生读回，保持既有文件作用域封条兼容。只能在空版本／激活／父操作和全新原生来源中首次登记，不能静默接管现有安装；同一来源不能重复登记。运行时引用、真实受管进程 fd 租约和可恢复 GC 使用同一维护器，真实桌面发现／授权与 Windows 继续由正式适配器实现。

调用方先构造明确隔离根的原生协调器，再以 `RuntimeLifecycle(operation.runtime_store, [operation])` 初始化；原生 transport 的认证仍由调用方负责。后续暂存、安装、恢复、移除、调用及回收经维护器入口执行，作用域 key 仍为根绑定的确定性摘要。盘点仅观察，不自动取消或完成原生请求；来源 pending、孤儿父日志、未登记来源、未知内容或原生／技能漂移均阻止回收。用户删除来源目录时直接拒绝，不能重建后解释为全新无引用。

[原生生命周期测试](deepseek_tests/test_native_lifecycle.py) 覆盖混合文件／原生作用域、原生无变化更新、初始与终态漂移、活动 fd、双原生锁、版本释放及 GC 隔离后的再次漂移保护。[完整运行时探针](validation/probe_native_lifecycle.py) 接收与第 7 章相同的固定参数，在空 PATH 下运行实际已安装工具、移除并完整回收真实树，模拟隔离后 Python 异常再恢复，重新暂存原树并重装；该异常不等于真实进程退出，也不证明真实宿主活动已被登记。

自举导出使用 `bootstrap_bundle.source_resources()` 保持新增模块的导入闭包、来源锁和资源清单；[导出源码导入测试](installer_tests/test_bootstrap_source_closure.py) 在独立解释器和仓库外路径实际导入文件与原生入口。资源包含这些源码不代表用户安装器已取得 DeepSeek 桌面授权，也不将自举自检或工程离线安装当作桌面验收。

## 9. 工具名称模式的只读诊断

[tool_name_profile.py](tool_name_profile.py) 是 D02 安装器的纯字节诊断核心，不启用服务端草案或写宿主配置。`admit(initialize_result_bytes, list_result_bytes, observed_prefix=actual_host_prefix)` 接收两份 result JSON；调用方先认证来源、对应 RPC 响应，并独立核实宿主实际包装前缀。它同时验证 portable-v1 两份确认、完整目录与摘要、规范名末尾标记及实际名称预算；旧服务忽略模式头、缺失／重复标记、未知别名或分页未齐均拒绝。

返回对象只保存本次不可变规范名到可见名表；`resolve(canonical_name)` 不解码或猜测未发现名称。新发现、配置／授权变化或目录变动后重建对象，不跨连接复用。`receipt()` 明确保留身份／宿主未验真、未写配置及未调用工具标识，不将这段核心当作业务权限或桌面支持证明。当前在线服务是否支持须实际检测，不能根据核心存在推断。

[13 项诊断测试](profile_tests/test_tool_name_profile.py) 和[固定名称夹具](profile_tests/fixtures/studio-tool-names.json) 覆盖真实名称样本、固定转义、两份确认、旧服务拒绝、摘要／歧义／边界、完整目录、JSON 防护及删除工具后快照更新；夹具只有名称与摘要，不携带身份或连接配置。整体用户安装器和服务端适配继续按计划实现。

### 9.1 随包只读快照命令

[固定快照驱动](installer_tools/tool_name_diagnostic.py) 与诊断核心已纳入自举资源和 source-lock；`vf-bootstrap tool-name-profile --request <请求文件>` 在资源完整性检查后使用随包 Python `-I -B` 执行，不依赖宿主 Python 或环境变量。工程自举未签名，不作为正式安装器或支持声明。

请求 JSON 只含 `schema`（`vf.tool-name-profile-request/v1`）、两份原字节摘要 `initialize_sha256`／`tools_sha256` 和另行观测的 `observed_prefix`。结果文件固定为请求同目录的 `initialize-result.json`／`tools-result.json`，不接受任意输入路径。目录须事先为当前用户所有的 0700，文件为单硬链接、无符号链接／额外 ACL 的 0600；入口不新建目录或修复权限。输入两次读取一致后才输出摘要收据，正文、路径、工具名和前缀不进入输出；本地字节关联不能证明真实连接身份。

[8 项快照入口测试](installer_tests/test_tool_name_diagnostic.py) 覆盖无写成功、空目录、旧服务、哈希不符、重复／越界请求、链接／FIFO／模式、缺目录及读取竞争；[原生随包探针](validation/probe_tool_name_bootstrap.py) 在空 PATH、禁网和禁止文件写入的 macOS 子进程中验证实际入口。探针用真实记录名称及明确合成的确认／描述，不能替代真实服务或桌面证据。Windows 私有存储尚未验真时明确拒绝，交叉编译不算执行。

## 10. Speech 普通参考派生

[派生 helper](speech_tools/derive_voice_reference.py) 经 Speech 明确白名单进入新候选的 `scripts/derive_voice_reference.py`；与同目录原字节的 `prepare_voice_profile.py` 共享 PCM／能力检查。使用发行集合的 Python `-I -B` 和显式固定摘要的受管 FFmpeg／FFprobe，不使用系统 PATH、凭据、后台实验融合或媒体生成服务。

共享检查器直接编译已核验的源码快照，避免常规加载器读取清单之外的既有 `.pyc`。测试包含匹配源文件时间戳／大小的合成缓存替换反例；`-B` 本身只禁止缓存写入，不证明缓存不会被读取。

调用方提供实际能力快照及明确音轨、选段、声道选择／等权混合、采样率和位深。新目录保存 `reference.wav` 与 `derivation.json`，原件／能力／工具摘要和实际 PCM 测量可追溯；输入改变、静音、源不足、能力限制或已有输出均拒绝。具体参数与失败边界进入公开包 `references/new-voice.md`，不是要求用户手工配环境的安装指南。结果保持候选／待听审／远端未登记；Windows 私有目录未经验证时拒绝。

[真实工具回归](speech_tests/test_derive_voice_reference.py) 使用合成 WAV／M4A，要求 `VF_SPEECH_TEST_RUNTIME` 指向已核验且含 `bin/ffmpeg`／`bin/ffprobe` 的实际工程运行时。以 Python `-I -B -m unittest discover -s src/desktop-skill-delivery/speech_tests -v` 执行；测试工具缺失即失败，不跳过后填通过。macOS 可对整个测试进程使用禁网沙箱；转码和包内独立运行不能代替真实 Speech、声线登记、听审或桌面验收。

## 11. 只读桌面身份发现

[host_discovery.py](host_discovery.py) 为 D02 提供 macOS 应用元数据观测。按准确 bundle ID 区分五个目标，独立记录 Antigravity IDE；显示名不影响产品分类，多份安装保留歧义。只扫描显式应用目录第一层，不启动程序、访问用户配置或查找凭据。每份观测绑定版本、构建号、Info.plist 和主可执行文件摘要，`revalidate()` 可在使用前重新检查；这不能替代整个资源包、签名、运行进程和桌面会话证据。

固定原生命令为 `./vf-bootstrap observe-hosts`，不接受额外参数。自举先校验资源，再用随包 Python 在空 PATH 下扫描默认应用目录；输出不含真实路径或显示名。有不可读／链接候选时返回部分观测、`scan_complete=false` 和退出码 1，未找到的目标保持未知，不误报未安装。Windows 暂返回未实现；此命令不写配置、不建立连接、不提高支持状态。

[9 项发现测试](profile_tests/test_host_discovery.py) 覆盖五端身份、IDE 排除、重复安装、版本／构建／可执行文件漂移、符号链接与 FIFO、畸形元数据、扫描期间目录变化及回执脱敏。它们与既有 13 项工具名称测试合计 22 项；导出源码闭包及实际随包命令另行验证。正式契约见[宿主发现设计](../../../docs/design/skills/details/README.agent-compatibility.host-profiles.md#36-安装器只读桌面发现)。
