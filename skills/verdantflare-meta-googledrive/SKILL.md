---
name: verdantflare-meta-googledrive
description: 使用已登录的 Google Drive 桌面映射盘管理项目图片、视频、生产和设计素材。项目 .gdrive 是指向映射盘项目目录的软连接，由客户端跨机同步；临时与可抛弃文件留 .tmp，Markdown 引用或显式指定的素材入 .gdrive（已进 Git 的文件豁免），被替代且无引用的旧版直接移除，不留历史。用于素材入库、引用审计、新机接入与本技能全局安装维护；不负责生成素材或代替人工审美审核。
---

# Google Drive 项目素材管理（verdantflare-meta-googledrive）

## 1. 编码规范

文档编码 `GD`；章节编码使用两位数字；需要图表时采用 `[GD][章节两位][字母与数字两位]`，例如 `GD03A1`。本技能为 `verdantflare-meta-*` 基础技能，不依赖当前仓库业务资产，可独立全局安装。

## 2. 范围与边界

本技能通过 Google Drive 桌面映射盘与项目 `.gdrive` 目录软连接管理跨机同步的大型素材，覆盖项目初始化与旧版目录接入、素材按内容 SHA-256 不可变版本入库、Markdown 引用审计与相对链接改写、摘要核验、被替代无引用旧版移除，以及本技能的全局安装维护。

职责边界以 `git ls-files` 状态判定：已被 Git 跟踪的文件豁免入库；未跟踪或被 `.gitignore` 排除的本地文件一旦被任意 Markdown 引用（或经用户显式指定跨机复用），必须入库 `.gdrive/managed/`。本技能不负责生成素材、不自动下载外部 HTTP 资源，也不代替人工做审美通过判定；Markdown 排版与链接格式检查交由目标环境的 Markdown 治理技能（如 `verdantflare-meta-markdown`）协作处理。

## 3. 能力清单与按任务读取

按当前要执行的原子能力直达对应入口；跨能力通用契约见 `00.common.rules.md`：

| 原子能力（能干什么活） | 入口文档                                                      |
| :--------------------- | :------------------------------------------------------------ |
| 素材入库               | [01.ingest.entrypoint.md](references/01.ingest.entrypoint.md) |
| 引用审计               | [02.audit.entrypoint.md](references/02.audit.entrypoint.md)   |
| 新机接入               | [03.setup.entrypoint.md](references/03.setup.entrypoint.md)   |

先读目标能力的 `entrypoint.md`，无需通读其他能力文档；跨能力软连接契约、双域分离、零冗余与人工目录保护见 [00.common.rules.md](references/00.common.rules.md)。

## 4. 工具与命令

[gdrive.py](scripts/gdrive.py) 负责软连接初始化、入库、引用审计、摘要核验与旧版移除，[install.py](scripts/install.py) 负责安装到统一安装根 `~/.agents/skills`，依赖见 [requirements.txt](scripts/requirements.txt)，回归测试为 [test_gdrive.py](scripts/test_gdrive.py)。全局调用时使用已发现的安装路径，不假设业务项目包含 `skills/`：

```powershell
python -X utf8 scripts/install.py
python -X utf8 scripts/install.py --vscode
python -X utf8 scripts/install.py --check-vscode
python -X utf8 scripts/gdrive.py --project '<project-abs-path>' init --drive-root '<drive-root>' --namespace github.com/<group>/<project>
python -X utf8 scripts/gdrive.py --project '<project-abs-path>' audit --doc docs/review.md
python -X utf8 scripts/gdrive.py --project '<project-abs-path>' put '<source-file>' --bucket pending --name shots/scene-01.png --reason markdown-reference
python -X utf8 scripts/gdrive.py --project '<project-abs-path>' verify
python -X utf8 scripts/gdrive.py --project '<project-abs-path>' remove 'pending/<old-versioned-file>' --apply
python -X utf8 -m unittest discover -s scripts -p test_gdrive.py -v
```

本地 VS Code 单目录 Markdown 预览安装与排障见 [03.setup.entrypoint.md](references/03.setup.entrypoint.md)，扩展源码位于 [package.json](resources/vscode-preview/package.json)。安装回读不代表桌面预览或跨机同步已验收。
