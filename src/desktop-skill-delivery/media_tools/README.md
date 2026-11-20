# 受管媒体工具构建与检查

## 1. 当前依据

- [主仓库媒体组件设计](../../../../docs/design/skills/details/README.agent-compatibility.integration.md#2110-受管媒体工具的源码构建)：来源闭包、平台边界及合成验证要求。
- [源码锁](media-source-lock.json)：FFmpeg、x264、zlib、pkgconf 和 NASM 的原始归档、URL 与 SHA-256。
- [Windows 交叉工具链锁](cross-toolchain-lock.json)：构建工具来源，不是用户运行依赖。

## 2. 构建输入和边界

从对应 URL 获取锁定原归档，保存在现有临时输入目录，连同两份锁文件使用。构建器只读取本地输入并核验摘要，不联网补包、不使用系统包管理器或写全局前缀。相同下载站点的摘要不等于发布者签名；签名、可信发行、一键安装与桌面业务验收另行完成。

当前构建宿主为 macOS arm64。原生目标使用本机 Command Line Tools；Windows amd64 目标使用锁定的 llvm-mingw 与本次从源码编译的 NASM。交叉工具链在完整核对相对链接图后解包至本次构建根，不加入系统 PATH。Windows 输出只完成构建和静态导入检查，仍须在真实 Windows 执行验证。

启用 libx264、zlib 和 GPL version3，关闭自动库探测、网络协议和设备输入。此组件供本地媒体检查与转码，媒体生成仍由业务 MCP 完成。候选携带全部五项原始源码归档、许可证、构建器和来源锁；交叉工具链归档为单独构建输入，候选保留其获取锁、许可证、解包收据与工具来源记录。

## 3. 隔离构建

从工作区主仓库根执行；`INPUT` 为已存在的输入目录，`OUTPUT` 为唯一临时根下尚不存在的构建子目录，父目录须已存在。Python 需支持本工具所用语法，不使用业务用户的环境补依赖。

```text
python3 verdantflare-skills/src/desktop-skill-delivery/media_tools/build_media_runtime.py --sources INPUT --work OUTPUT --target macos-arm64 --jobs 4
python3 verdantflare-skills/src/desktop-skill-delivery/media_tools/build_media_runtime.py --sources INPUT --work OUTPUT --target windows-amd64 --jobs 4
```

每轮使用新的 `OUTPUT`，失败后保留原日志与目录，不原地覆盖。编译作业最多 4 个；记录各步骤退出码和脱敏命令，失败不生成成功收据。成功目录为 `OUTPUT/candidate/`，含 `bin/`、`sources/`、`licenses/` 和 `build-receipt.json`。候选固定不可安装、未公开发行、未通过桌面验收；编译期间来源锁或构建器改变会拒绝出具成功收据。

## 4. 原生合成检查

macOS 使用下面的独立入口，`PROBE_OUTPUT` 同样须为新的临时子目录：

```text
python3 verdantflare-skills/src/desktop-skill-delivery/media_tools/probe_media_runtime.py --component OUTPUT/candidate --work PROBE_OUTPUT
```

工具将组件复制后迁移至含空格父目录，确认旧路径不存在，清空 PATH 并显式执行该组件的二进制。用程序生成的 RGB 和 WAV 检查 H.264／AAC MP4、流和时长、PNG 抽帧、解码像素误差、FLAC 无损往返与音频重采样；保存真实能力清单、逐步日志、命令和输出摘要，检查二进制前后字节未变。合成检查不占用 110 个技能环境验收行，也不替代真实 MCP 业务链。

来源与工具链边界测试不调用编译器：

```text
python3 -m unittest discover -s verdantflare-skills/src/desktop-skill-delivery/media_tests -v
```
