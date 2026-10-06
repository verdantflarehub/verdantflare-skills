# 输出与导演交接

`breakdown.json` 是本地草案，不是剧本审定稿。它包含 `schema_version`、源文件路径与 SHA-256、模型与生成时间，以及四组内容：

- `facts`：`kind`、`value`、`evidence`。`value` 和 `evidence` 都能在原文中找到，但语义分类仍需人审。
- `beats`：`action`、`evidence`。动作必须是引文中的逐字片段；是否构成独立镜头仍由导演判断。
- `inferences`：`idea`、`basis`、`needs_review=true`。即使依据来自原文，也不得升格为角色或故事事实。
- `questions`：模型提出的待确认问题。
- `rejected`：缺失原文证据、字段错误或事实值不能从证据直接定位的项及理由。

先阅读 `rejected` 和 `inferences`，再核对 `facts` 与 `beats` 的语义是否忠于剧本。只把人工认可的事实交给 `x-verdantflare-short-drama-director`，由 Director 建立 `treatment.md`、`plan.json`、Shot 时间线和资产需求。不要将本地 `source.path` 当作 Station Artifact 或正式 `project_id`；用户资产主文件仍按项目存储策略管理。
