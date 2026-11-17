---
name: verdantflare-comfyui
description: 通过 Studio MCP 受控操作 VerdantFlare ComfyUI 交互实例与 comfyui-api 无头生产池，完成开源模型依赖齐套（model.*）、白盒故障诊断与单实例自愈（diag.* / repair.*）、画布冒烟及参数化工作流 API 提取发布与调用（api.*）。用于 ComfyUI 工作流排障、模型按需下载热加载、工作流固化发版与批量无头生产；不用于获取裸集群写或 exec 权限、引入付费模型、在线安装第三方节点或替代人工视觉终审。
---

# VerdantFlare ComfyUI 受控工作流与模型治理（verdantflare-comfyui）

## 1. 范围与边界

本技能封装 VerdantFlare ComfyUI（`comfyui-v1` 25 工具契约）从画布研发排障、开源模型规范供应到参数化 API 生产交付的标准五步闭环：在零裸集群写权限与零 `kubectl exec` 前提下，通过 Studio 单一网关完成模型目录核查与受控 CAS 拉取、白盒执行追踪与单实例自愈、沙箱冒烟，以及 `WorkflowAPISpec v1` 不可变版本发布与无头调用。

普通单次文生图或视频生成（已挂载至 `Image MCP` / `Video MCP` `本地` 渠道的现成模型）直接交由 `verdantflare-image`、`verdantflare-video` 执行；项目与不可变素材同步与 `verdantflare-project`、`verdantflare-artifact` 协作。本技能不请求集群写权限、不触碰需付费解锁的模型（`paidAccess` / `Early Access`）、不在运行时动态安装自定义节点，也不以接口跑通代替人工审美终审。

## 2. 按任务读取

| 当前任务                                                   | 读取资料                                     |
| :--------------------------------------------------------- | :------------------------------------------- |
| 区分交互实例、无头 `comfyui-api` 与媒体 `本地` 渠道入口    | [分类与边界](references/01.base-category.md) |
| 执行模型齐套、白盒排障自愈、工作流发布与无头调用五步闭环   | [执行流程](references/01.base-workflow.md)   |
| 核对零越权、禁付费模型、哈希配额、游标门禁与幂等键硬性约束 | [规则约束](references/01.base-rules.md)      |
| 检查各阶段完成判据、标准错误码恢复与交付汇报要素           | [验收标准](references/01.base-acceptance.md) |

先读当前任务对应专册；仅做无头已发布 API 调用时无需读取交互会话与实例重启细节。

## 3. 工具与命令

本技能全部操作经宿主已注册的 Studio MCP 连接执行，使用环境变量绑定的真实主体会话令牌，不内置绕过网关的独立直连脚本：

```text
# 1. 交互研发与受控排障入口（绑定指定实例，如 comfyuiA）
POST ${STUDIO_MCP_URL}/comfyuiA          # session.* / workflow.* / task.* / diag.* / repair.* / model.* / api.*

# 2. 无头生产调用入口（逻辑隔离执行池，不占用个人画布编辑锁）
POST ${STUDIO_MCP_URL}/comfyui-api       # api.list / api.describe / api.invoke / task.get / task.cancel / diag.execution_trace / model.list / model.status
```

执行前先通过当前连接执行 `tools/list` 核对实际已启用工具集与真实 `project_id`；完整工具调用顺序、参数契约与退避恢复策略见[执行流程](references/01.base-workflow.md)。
