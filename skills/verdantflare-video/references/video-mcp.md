# Video经Studio调用说明

本页描述Skill与领域Video服务的边界，不是Runtime或供应商API手册。工具名、参数、渠道和输入规格需在当前Studio会话中核实；认证与旧CLI差异见 [环境说明](../../ENVIRONMENT.md)。

## 工具发现与创建

现有网关注册 `video.create`、`video.status`、`video.result`。历史 `video.generate` 只有在当前宿主实际注册且语义明确时才使用，不能向网关发送未注册别名。工具注册、渠道已配置、推理就绪、引用可读是不同检查。

创建示意参数如下，所有占位标识在提交前解析为真实服务返回值；渠道和12秒示例不代表每个部署都支持：

```json
{
  "project_id": "<当前Studio项目ID>",
  "idempotency_key": "gen_007_v1/attempt_01",
  "model": "minimax-h3-ref2va",
  "route": "<已解析且可用的渠道>",
  "prompt": "<已保存的单元提示词>",
  "duration_seconds": 12,
  "aspect_ratio": "16:9",
  "references": {
    "images": [{"artifact_id": "<Video可读取的项目内原生引用>", "purpose": "identity"}],
    "videos": [],
    "audios": []
  }
}
```

每次创建显式提供 `project_id`、幂等键和已解析的 `route`，即使旧Schema把字段标为可选。业务模型 `minimax-h3-ref2va` 与推理渠道是不同字段；不把 `h3-vdn`、`h3-sol`、`h3-singularity` 等渠道名擅自填成模型。

输入模式按 [H3输入模式](input-modes.md)；时长、画幅、参考数量和音频支持同时满足网关与所选渠道要求。静态Schema与匹配部署版本的能力说明不一致时，记录并核实差异，不仅凭历史枚举断言模型不支持，也不绕过网关校验尝试收费提交。

## 渠道解析

- 用户指定模型/渠道时保留该选择，先核实实际映射和就绪状态；不可用时不静默切换。
- 未指定时使用当前宿主明确配置的默认值，并把解析后的渠道写入请求。文档中的历史默认值、工具Schema默认值和列表中的第一个可用项都不能单独替代宿主配置。
- H3原版、Sol等名称表示模型族或推理路线，不保证采用同一优化配置。实际路线、Runtime版本写入尝试记录，不凭模型名宣称运行了某个引擎。
- fal适配器使用H3 Ref2VA；其端点、供应商凭据和供应商任务ID由服务端管理。工具存在但fal未配置时，不能由Skill读取 `FAL_KEY` 补交请求。
- 已有任务按记录的原模型、渠道和任务ID恢复，不能以当前默认值重新生成。

## 素材引用转换

中央 `ContentRef` 固定 `store_id + artifact_id + version_id`；现存Video服务可使用不同的原生Artifact格式。按宿主受控适配取得同项目的可读引用，再填入 `references`，不能把Image原生ID、中央ID、World显示名或本地路径直接互换。

World输入先解析已授权固定版本，再导入或关联当前项目。记录来源ContentRef、Video引用、SHA-256与用途映射，图片编号按实际列表顺序与Prompt一致。

中央 `artifact.read(mode=download)` 返回的路径仍需鉴权，不是匿名HTTPS对象URL。旧Video导入适配只接收允许来源的HTTPS对象，不能据此假定支持中央受控路径。引用转换缺失时报告这一接入缺口，继续独立的本地准备和图像工作；不伪造引用，不把令牌放进URL，不公开私有素材来绕过导入限制。

## 状态与结果

创建成功后先保存服务返回的原始任务ID，再轮询。当前响应可能同时返回 `task_id` / `video_task_id`；按当前Schema传入受支持字段，保留原值，不改写成UUID。常见状态为 `queued`、`running`、`succeeded`、`failed`、`cancelled`；未知状态按协议错误处理。

`video.status` 的失败信息仅在脱敏后进入记录，不输出签名URL、内部路径或供应商原始响应。只对成功任务调用 `video.result`，保存原生Artifact、输入摘要、模型/渠道、Runtime版本及媒体元数据；下载按Studio受控内容路由，不把 `/mcp` 当下载根地址。

原生结果返回不代表中央Project归档已完成。有输出适配时用可信来源登记；没有时可在授权范围内通过现有工作副本客户端显式导入校验后的文件为 `user_import`，保留原生任务、Artifact和摘要映射，不伪造 `task_output`。

Project提交保存文件、`run_refs`、Prompt和审核记录。MD作为普通文件，`domain_documents` 只用于服务支持的领域JSON。技术验收通过仍是候选；不自动选用或注册World。
