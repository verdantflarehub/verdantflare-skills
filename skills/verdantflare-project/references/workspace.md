# Project 工作副本

## 1. 客户端与配置

复用 Studio 发布的 `studio-workspace`。已核对版本 0.5.50 支持 `tools`；执行前用 `--version`、`--help` 确认现有二进制。
来源为 Studio release CI 的 `studio-v0.5.50-workspace-windows-amd64` 或 `studio-v0.5.50-workspace-linux-amd64` 产物。
缺少客户端时可先用宿主 MCP 处理不依赖本地工作副本的任务，不自动安装软件或改全局配置。

进程环境提供 `STUDIO_MCP_URL`、`STUDIO_MCP_BEARER_TOKEN`，只使用 HTTPS 或本机 loopback HTTP。
不要求 Cookie 或 CLI 登录，不把 Bearer 放入参数、JSON、`.vf` 或输出。
配置文件作为数据解析，不通过 shell 执行；子进程输入输出显式使用 UTF-8。

## 2. 打开与取文件

本地目录需先存在；显式传服务 project_id、用户选定目录及连接别名。
别名只标识连接，不替代项目或存储 ID。

```text
studio-workspace tools
studio-workspace open --dir <dir> --alias <alias> --project-id <project-id>
studio-workspace status --dir <dir> --alias <alias> --project-id <project-id>
studio-workspace fetch --dir <dir> --alias <alias> --project-id <project-id> --file-id <file-id>
```

尖括号内容需替换为真实参数，不能原样调用。`open` 建立服务清单投影，并不承诺所有文件已经下载。
要读取特定历史修订，直接用 `project.open` 的 revision_id；不要假定 CLI 有未出现在 `--help` 中的历史 checkout 参数。

`.vf/project.json` 由客户端管理，`.vf/local.json` 是本地状态；不要手写、上传或复制它们充当服务数据库。
fetch 默认上限 1 GiB，只有任务需要且服务允许时才调整 `--max-bytes`。

## 3. 显式保存与导入

```text
studio-workspace save-texts --dir <dir> --alias <alias> --project-id <project-id> --input texts.json
studio-workspace save-files --dir <dir> --alias <alias> --project-id <project-id> --input files.json
studio-workspace import --dir <dir> --alias <alias> --project-id <project-id> --input imports.json
```

- texts.json：用户选定的既有文本 file_id 数组。
- files.json：对象数组，每项为工作副本相对 path、role、mime；替换已有文件时附 file_id。
- imports.json：对象数组，每项为 source_path、目标相对 path、role、mime；目标已存在时拒绝覆盖。

这些命令会保存内容并提交 Project，不适合“只存 Artifact，暂不加入项目”的请求。
原文件保留。来源目录不自动扫描，本地文件缺失不自动生成 remove。

## 4. 恢复与冲突处理

```text
studio-workspace resume --dir <dir> --alias <alias> --project-id <project-id>
```

中断后先运行 status；存在待恢复上传或提交时才运行 resume，复用原 journal。
没有待恢复操作时不把 resume 当作健康检查，正常保存后用 status 与固定修订回读确认结果。
本地后续编辑应保留，不能用服务回读内容覆盖它来制造成功。

`switch-head` 仅在无本地变更和待恢复提交时使用，切换基线后按需 fetch。
如使用 `conflict` / `resolve`，先核对当前版本帮助与返回的冲突预览，明确合并后再提交，不猜测解决参数。

修订响应恢复的语义见[修订与恢复](revisions.md)。
