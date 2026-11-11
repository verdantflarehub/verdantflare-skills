---
name: verdantflare-agent-antigravity
description: 将 Google Antigravity（双子座）封装为标准 MCP 服务与 CLI 调度接口，供其他智能体直接向 VS Code 前台 Antigravity 面板注入任务、续接对话、监控执行状态并读取产物与轨迹日志。用于跨智能体派单给双子座执行、查询双子座会话与产物、注册或维护 Antigravity MCP 服务；不替代上游方案设计或人工终审。
---

# Antigravity 智能体 MCP 桥接与跨智能体调度（verdantflare-agent-antigravity）

## 1. 编码规范

文档编码 `AA`；章节编码使用两位数字；需要图表时采用 `[AA][章节两位][字母与数字两位]`，例如 `AA02A1`。本技能为跨工作区全局技能，不依赖特定仓库业务资产，可独立全局安装。

## 2. 范围与边界

本技能将 VS Code 中的 Google Antigravity 扩展（底层由 `agy.exe --hub` 提供 `LanguageServerService` Connect-RPC 服务）与无头 `agy.exe` CLI 封装为统一的 `stdio` MCP 服务端及命令行工具。其他智能体（如负责方案与评审的智能体、负责底层工具链的智能体）可通过本技能默认向 **VS Code 前台 Antigravity 面板** 实时注入指令、新建或续接指定会话（`cascadeId`）、轮询执行状态并读取 `brain/<conversation-id>/` 下的产物与 `transcript.jsonl` 轨迹。本技能只负责跨智能体通信调度与 MCP 服务自身维护，不替代方案设计或人工审批。

## 3. 按任务读取

| 当前任务                                       | 读取资料                                     |
| :--------------------------------------------- | :------------------------------------------- |
| 调用模式选择（前台面板注入 vs 后台 CLI）与判据 | [分类与边界](references/01.base-category.md) |
| 跨智能体派单、状态回收、全局安装与 MCP 注册    | [执行流程](references/01.base-workflow.md)   |
| 交接书传参约束、焦点保护与底层 RPC 协议契约    | [规则约束](references/01.base-rules.md)      |
| 静态检查、单测、Live Hub 连通与面板注入验收    | [验收标准](references/01.base-acceptance.md) |

先读当前任务对应专册，不要求通读全部专册。

## 4. 工具与命令

主脚本 [agy_mcp_server.py](scripts/agy_mcp_server.py) 同时提供 MCP `stdio` 服务与 CLI 子命令，[install.py](scripts/install.py) 负责安装到 `~/.agents/skills/` 并向本机其他智能体注册 MCP，回归测试见 [test_agy_mcp_server.py](scripts/test_agy_mcp_server.py)：

```powershell
python -X utf8 scripts/agy_mcp_server.py status
python -X utf8 scripts/agy_mcp_server.py list
python -X utf8 scripts/agy_mcp_server.py dispatch "请读取并执行变更交接书" --workspace .
python -X utf8 scripts/install.py --replace --register-mcp
python -X utf8 -m unittest discover -s scripts -p "test_*.py" -v
```
