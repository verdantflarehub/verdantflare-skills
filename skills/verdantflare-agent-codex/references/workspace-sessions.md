# 当前工作区会话列表补丁

## 1. 适用边界

这是可选的实验性本地补丁，适用于 Windows 本地 VS Code `openai.chatgpt` 扩展。旧基线由包内 `resources/workspace-sessions/adapter.json` 声明，版本为 `26.1106.51428`；新增 `adapter-26.1111.21308.json` 适配 `26.1111.21308` 并已完成本机安装与验收。按实际版本选取独立锚点、绑定与摘要，未知版本不写入。它按当前工作区过滤首页、历史下拉、搜索和数量，并通过双阶段目录观察同步原生改名标题，不删除会话、不修改认证或按 ID 调度能力。

路径按多根工作区并集和完整目录边界匹配，支持子目录、Windows 大小写及宿主规范路径。兄弟 Git worktree 只有加入工作区才显示。无工作区、未知 cwd、远端或云端会话不推断归属；本机筛选不是权限隔离。2026-11-12 在适配版本的 Windows 本地 VS Code 完成脚本、打包文件与双项目真实体验验收；用户手工确认移除临时诊断后的启动、会话归属、历史搜索、计数及旧会话恢复。此结论不扩展到其他版本或宿主。

## 2. 探测与启用

从本技能目录执行，要求 Python 3.11 或更新版本。探测和状态检查不写扩展或补丁状态；存在多个未能唯一识别的扩展时，通过 `--extension` 指定目标安装目录。

```powershell
python scripts/workspace_sessions.py probe
python scripts/workspace_sessions.py status
python scripts/workspace_sessions.py apply
```

也可在已授权的完整技能安装中显式应用：

```powershell
python scripts/install.py --replace --workspace-sessions
```

普通技能安装仅分发管理工具，不修改扩展。`--register-mcp` 仍是独立选项，应用显示补丁不要求重新注册或登录。更新已有安装前核对配置与安装副本独有改动，不能借 `--replace` 覆盖无关配置。

安装输出的 `workspace_sessions_check` 始终报告实际补丁兼容与安装状态，即使未请求启用；扩展再次升级为未知版本时明确返回未适配的 `blocked` 原因。此检查发生在命令执行时，不是后台升级监控，不能保证 VS Code 自动升级时主动弹窗。

应用返回 `installed-reload-required` 只表示磁盘文件已验证写入；待目标窗口没有活动任务后重载窗口或插件 Webview，再检查页面。不得为检查主动打断其他项目任务。

## 3. 状态与恢复

- `compatible`：当前原始文件和依赖摘要符合适配，可以应用。
- `installed`／`already-installed`：当前文件等于本包预期补丁；不证明现有窗口已经加载。
- `installed-older-patch`：磁盘为已知旧补丁，先回滚再应用新修订。
- `interrupted`：已知的部分写入状态，回滚后重试。
- `blocked`：版本、摘要、备份、锁或安装信息不符。读取原因，保持原文件，不绕过检查盲目写入。

备份、清单和操作锁在本机用户数据目录 `verdantflare-agent-codex/workspace-sessions/` 下，按扩展目录分开保存，不随技能分发。锁残留时先查明记录的进程是否仍存在及文件状态，不能自动删除锁后并发写入。

```powershell
python scripts/workspace_sessions.py rollback
```

回滚仅恢复当前仍为已知原始／补丁内容的文件，备份也必须通过摘要检查。扩展升级或用户修改后不覆盖新文件。升级后重新运行 `probe`；不兼容时需要维护适配并重新验证，不因扩展目录自动发现成功而声称补丁兼容。

## 4. 验证与交付

```powershell
node --test scripts/test_workspace_sessions.mjs
python -m unittest discover -s scripts -p "test_*workspace_sessions*.py"
```

检查覆盖路径边界、分页、迟到响应、失败和回滚；真实打包文件的锚点、依赖摘要及语法另行验证。技能安装后对比维护源与安装副本哈希。

真实体验检查应同时打开两个项目窗口：各自首页只显示本项目会话，历史搜索不出现其他项目，计数与列表一致，旧会话仍可打开；工作区切换和回滚后再次检查。未执行的项目明确记录为未验证。原有按 ID 调度回归不通过时不能交付为完成。
