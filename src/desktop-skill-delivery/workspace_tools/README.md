# Studio workspace CLI 工程组件

## 1. 当前依据

- [共享 CLI 交付设计](../../../../docs/design/skills/details/README.agent-compatibility.integration.md#214-共享-cli-的平台交付)：源快照、离线构建及分层验收。
- [Go 工具链锁](go-toolchain-lock.json)：固定官方构建输入，不是用户运行依赖。

## 2. 用途与前提

本工具准备并校验未签名的内部工程组件，构建既有 Studio `cmd/workspace`，不提供用户安装器。构建机器为 macOS arm64，工具需要 Python 3.12 标准库、Git 与系统 `otool`；不使用本机 Go、展开的开发模块缓存、网络取包、Docker、用户身份或桌面配置。

原始 SDK 归档须与工具链锁的官方 SHA-256 和字节数一致。模块输入须含 `module-inputs.json`、原始 zip／mod／info／ziphash，绑定原项目 `go.mod`／`go.sum`；模块 ZIP 内容按 Go h1 校验，原始归档 SHA-256 另行保存。缺材料即失败，不删减原项目模块图或联网补齐。

## 3. 准备源码候选

在工作区内显式传入源目录和已准备输入，输出目录必须不存在，其父目录必须已存在：

```sh
python3.12 workspace_delivery.py prepare \
  --source path/to/verdantflare-studio \
  --modules path/to/module-inputs \
  --sdk-archive path/to/go1.26.11.darwin-arm64.tar.gz \
  --sdk-lock go-toolchain-lock.json \
  --output path/to/new-source-build
```

输出含 `candidate/source/` 主模块允许集合、`modules/` 原材料、`tooling/` 本工具、`licenses/`、`source-status.json` 和 `source-lock.json`。SDK 单独提供，不复制为用户运行时。允许集合由两个目标的实际依赖、嵌入资源及三组原有测试解析得到；保留 Git 提交和实际工作树修改标记，不宣称源目录完全干净。

调用方通过独立可信交接记录保存返回的 `sha256`，它是 `source-lock.json` 原字节的摘要。校验时必须提供此值；从待验候选自行读取摘要不能证明来源可信。

## 4. 独立离线构建与接收

将完整源码候选复制到另一工作位置，使用其随包工具和固定 SDK 原归档。以下摘要占位符均须由调用方替换为交接记录的真实值，不能直接运行占位符：

```sh
python3.12 candidate/tooling/workspace_delivery.py verify \
  --candidate candidate --sha256 SOURCE_LOCK_SHA256
python3.12 candidate/tooling/workspace_delivery.py build \
  --candidate candidate --sha256 SOURCE_LOCK_SHA256 \
  --sdk-archive path/to/go1.26.11.darwin-arm64.tar.gz \
  --output path/to/new-cli-build
python3.12 candidate/tooling/workspace_delivery.py verify-runtime \
  --candidate path/to/new-cli-build/candidate --sha256 RUNTIME_DESCRIPTOR_SHA256
```

每次构建创建独立模块／编译缓存，关闭 Go 环境、workspace、自动工具链和代理。仅编译 macOS arm64／Windows amd64 两份 CLI；三组原有测试实际运行于 macOS，失败或跳过均拒绝。日志中的工作目录和个人路径脱敏。

构建输出 `workspace-runtime.json` 绑定二进制、完整 `build-source/` 和许可证副本；返回的 `sha256` 是该描述文件原字节摘要。接收校验不执行候选程序，核对全部文件／模式、源版本、模块 h1、SDK 绑定、许可证副本及两个平台的二进制格式。描述文件中的测试计数是构建声明，独立审核仍须查看原始日志或重新执行，不能把读到计数当成重新通过测试。

## 5. 原生迁移探针与限制

```sh
python3.12 candidate/tooling/workspace_delivery.py probe \
  --candidate path/to/new-cli-build/candidate --sha256 RUNTIME_DESCRIPTOR_SHA256 \
  --output path/to/new-native-probe
```

探针校验候选后，复制 macOS 二进制并将父目录移到含空格的新位置，确认旧位置消失；空 PATH、无业务身份执行版本、帮助和缺身份拒绝，再复核字节。结果保存在 `probe-receipt.json`，不调用 MCP 或修改宿主配置。

Windows 交叉编译和 PE 检查不证明 Windows 执行；原生帮助不证明 MCP、媒体生成或五端业务验收。候选始终保留 `installable=false`、未签名、未公开分发等标记。Studio 主模块许可证授予尚未核实，工具保留原许可证发现情况，不能替项目作许可证授权。签名、公证、正式分发和桌面验收须分别取得真实证据。
