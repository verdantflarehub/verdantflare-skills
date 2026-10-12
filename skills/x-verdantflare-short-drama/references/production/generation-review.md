# 生成准备与人类审核交接

## 1. 何时使用

进入新制作阶段、批量生成或需要新消费额度时，先核对本轮固定准备与真实用户决定。沿用已批准范围和剩余额度，不要求同范围每次调用重新确认。五类节点为制作计划、剧本包、素材试样、镜头试样、粗剪；审核范围按实际对象分别放行。

人物、服装、道具、场景、声音、配乐六类都保留。声线／对白／音效和音乐单独试听；视觉试样通过不授权音频批量。已有合格成果直接用来审核，不为审核补造样例。

## 2. 准备文件与检查

按[包内 Schema](generation-preparation.schema.json)形成普通项目 JSON。固定项目及依据修订，逐项记录 file_id、完整 ContentRef 和用途；目标、状态、指令、执行渠道和额度须明确。覆盖清单注明本轮未涉及或缺失的类别，不把声音与配乐省略。

决定使用真实用户消息／Studio 审核引用，尚未获得创作放行时写 pending。软件功能验收不授权作品生成。`max_calls` 包含重试，`max_retries` 不能额外增加总调用数；零次为仅准备。费用／Token 未知用 null，不写零伪装免费。先说明未知项，实际消费边界由获授权执行端核对。

依赖 Python 3.10+ 和 jsonschema 4.x；缺依赖时明确报告，不自动安装。以下命令从本技能目录执行：

```text
python3 scripts/validate_preparation.py preparation.json --project-open basis-open.json
python3 scripts/validate_preparation.py preparation.json --project-open basis-open.json --check-handoff
```

`basis-open.json` 为现有 Project MCP 对固定修订的实际响应，不能用最新 head 冒充。按脚本 `scope_digest` 对 schema_version、kind、project_id、basis_revision_id、stage、scope、basis、coverage 计算固定摘要；依据或额度改变后保留旧记录，不继承旧批准。

默认退出码 0 只说明结构和已提供的固定引用检查通过；`--check-handoff` 缺决定、渠道、额度或固定回读时退出 2，结构／引用错误退出 1。任何结果都保持 execution_authorized=false：这是交接检查，不能证明批准真实性、实时余额或服务端硬限额。

## 3. 提交与恢复

使用现有 Project 技能保存准备及审阅说明，取得固定修订后通过 Artifact 技能回读字节并核对摘要；不另造上传客户端。没有媒体输出时不登记成功候选、不修改选用或 World。来源和输出按同一真实项目维护。

达到阶段范围、消费额度或允许返修次数即停止新增生成；未知结果查询原任务、原项目提交，有限轮询。等待人工决定可保存、归档及做必要的确定性检查，不继续批量编写提示词、模型互审或媒体生成。每次自动评审同样计入调用和 Token 范围。

## 4. 当前实施边界

准备文件检查始终不提供可信审批或实时余额。执行时按 Project 技能的[生成控制](../../../verdantflare-project/references/generation-control.md)发现实际服务能力，归档待审清单、回读服务端决定并执行固定操作；未部署或未覆盖的适配器保持待确认。不能因源码已实现、记录 approved 或校验通过宣称线上已授权。

客户端会话 Token 仍受宿主能力约束。真实决定、执行请求与剩余额度尚未核实时，停在交接准备阶段；Skill 不使用登录密码替用户批准真实创作额度。声音和配乐的实际执行及独立试听审核继续保留，不借用视觉批准绕过。
