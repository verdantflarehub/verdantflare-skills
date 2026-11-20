# Hub 技能包联调示例

## 1. 现在可以做什么

这份例子带有 11 个真实 Skill ZIP、完整依赖和摘要、原候选验证器，以及可直接读取的 Hub 测试 JSON。Hub 现在可以继续做候选包解析、技能与依赖展示、工程下载、摘要校验及异常提示。所有命令均离线，无需 VF 账号、Token 或源码仓库，开发环境使用 Python 3.10+。

示例为 `sample=true`，固定候选 `0.2.0-integration.6`，`publishedReleaseId=null`、`distribution=unavailable`、`installable=false`。下载得到真实包字节；安装器和五端 profiles 仍在制作，当前命令不执行安装或 MCP。正式身份、签名、下载资格及 HTTP API 继续按双方设计确定。不要把本地样例字段当成正式 Center API。

## 2. 三分钟接收

将 ZIP 解压至全新的工程目录。在解压目录运行以下命令；Windows 可把 `python3` 替换为 `py -3`：

```text
python3 hub_example.py verify --root .
python3 -B tooling/delivery.py verify candidate
python3 hub_example.py plan --root . --skill verdantflare-project
python3 hub_example.py download --root . --skill verdantflare-project --output ../hub-example-download
```

前两步分别返回 `example_integrity_verified` 与 `integrity_verified`，均含 11 包。Project 计划按依赖顺序返回 Artifact、Project，合计 37509 字节。下载目录必须不存在，所得两个 ZIP 保持原字节，`download-receipt.json` 记录每包摘要；没有对 Skill 目录或宿主配置的写入。命令不会请求网络，也不会启动桌面程序。

`--skill` 可重复指定；例如 `--skill verdantflare-image --skill verdantflare-video`，共享依赖只返回一次。预览与缺安装器场景是正常计划结果，退出码 0 不表示安装成功。输入错误、未知技能、篡改、复用输出目录或被撤回的模拟下载返回退出码 2。

## 3. Hub 直接读取的文件

- [hub-example.json](hub-example.json)：11 个技能、真实包路径／大小／摘要、依赖、五端双系统状态、示例标记和缺少的安装器。
- [Project 依赖计划](examples/project-preview.json)：可直接写解析测试的期望输出。
- [缺少安装器](examples/project-missing-installer.json)：保留候选资料并提示尚不可安装。
- [模拟撤回](examples/project-withdrawn.json)：禁止获取样例；原候选未被真实撤回。
- [模拟摘要错误](examples/project-checksum-mismatch.json)：错误提示测试夹具；原始包没有被破坏。
- [WorkBuddy 选择样例](examples/workbuddy-selection.json)：Windows／WorkBuddy／Project 的本地选择数据，未测状态和空环境引用保留。
- [原始候选清单](candidate/release-manifest.json)：`vf.skills.handoff/v2`；来源锁与逐文件清单均在 `candidate/`。
- [原校验器说明](tooling/README.md)：说明原始 11 包内容与完整性验证。

下载实现应从服务器自己的受控制品定位映射到这些真实字节，不能把本地相对路径直接当成生产 URL。能力组合 B01—B05 继续由既有业务设计维护，此例只展开清单里的技能依赖。

## 4. 可立即跑的异常用例

```text
python3 hub_example.py plan --root . --skill verdantflare-project --scenario missing-installer
python3 hub_example.py plan --root . --skill verdantflare-project --scenario withdrawn
python3 hub_example.py download --root . --skill verdantflare-project --scenario withdrawn --output ../must-not-exist
python3 hub_example.py download --root . --skill verdantflare-project --scenario checksum-mismatch --output ../must-not-exist
```

计划结果分别含 `INSTALLER_NOT_DELIVERED` 与 `SIMULATED_RELEASE_WITHDRAWN`；两个拒绝下载命令退出码均为 2，且不创建输出目录。实际损坏检测可在另外复制的一份示例中修改包字节后运行 `verify`，返回 `EXAMPLE_FILE_HASH_MISMATCH`；勿改保存的原件。

## 5. 后续替换与交接边界

本轮复用原 r39 已审候选和其原校验器，不覆盖原包或伪造新的发行审核。原交接 ZIP SHA-256 为 `529deb60b309edee7cb2c5b98c0b8a2fead15650a4811e1efa2a81dffca3102b`；原候选清单 SHA-256 为 `915df319189f12ef3b494c43c92eb27673bc5fdcddbb7830d3e6f307e6b48468`。新外层示例摘要由发送方另行给出；同包内摘要只用于完整性检查，不自证发布者身份。

Hub 完成这些工程联调后，正式安装器、可移植运行时、五端 profile、发行签名和真实验收由后续同版本交付替换。示例不设置生产入口，不签发凭据，不登记已安装、不把十环境 `not_run` 改为通过。
