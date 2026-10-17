# 生成准备与恢复

## 1. 任务与调用授权

MCP Token 即调用授权，由 Studio 解析为授权用户。沿用用户当前任务、模型渠道、固定版本和明确的次数或费用限制，不增加独立审批、密码确认、批准清单或默认零额度。通过实际媒体工具执行，见[MCP 授权与恢复](https://github.com/verdantflarehub/verdantflare-skills/blob/e51e12f52023bbca00c706ef818efca6d7faba9d/skills/verdantflare-project/references/mcp-authority.md)。

人物、服装、道具、场景、声音与配乐按本轮任务保留完整依据。准备记录用于说明输入、用途与结果；它不是 MCP 调用许可证。生成结果保留为候选，选用和发布依照用户意图执行。

多轮修订、素材复用和批次依赖按[执行方案](execution-planning.md)维护当前依据。明确的用户追加授权覆盖对应旧额度；代理曾建议的数量不是用户硬上限。只在实际授权或依赖缺口处暂停，原任务查询、传输恢复与新收费生成分别记录。

## 2. 可选准备校验

需要固定准备文件时使用 `generation-preparation.schema.json`，校验固定修订和引用。用户没有要求制作准备文件时，不把它作为调用前置条件。旧记录中的 decision 仅保留来源说明，不决定 Token 权限；额度字段只记录用户明确给定的限制，不自行填零。

依赖 Python 3.10+ 和 jsonschema 4.x。从本技能目录运行：

```text
python3 scripts/validate_preparation.py preparation.json --project-open basis-open.json
python3 scripts/validate_preparation.py preparation.json --project-open basis-open.json --check-handoff
```

默认退出码 0 表示结构和所提供的固定引用校验通过；`--check-handoff` 只检查执行信息、明确限制和依据完整性，缺项退出 2，结构或引用错误退出 1。脚本不读取 Token、不判定调用授权，也不提供服务端预算控制。

## 3. 提交与恢复

通过现有 Project／Artifact 技能保存本轮准备和成果，保留固定引用及 SHA-256。没有媒体输出时不登记成功候选。响应未知先查询原任务，保持原模型、渠道、任务 ID 与幂等键；不得重发生成来掩盖未知结果。

按用户指定的范围与次数执行，达到明确限制后保存进度；未提供的限制不自行编造。声音、配乐和画面分别保留候选与质量核验，MCP 执行授权不自动成为选用或公开发布指令。
