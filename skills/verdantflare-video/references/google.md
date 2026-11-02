# Google 渠道调用

## 1. 模型与模式

先分页发现当前 Studio 的 Video 工具和输入 Schema，再读取 `video.capabilities`。使用 `route=google`，`--model` 取对应能力行的 `model` 或 `models[]` 真实别名；面向用户展示注册记录提供的简称。不能把旧 Veo 标识或示例名当作仍在服务的模型，也不自动把退役模型替换为新 Omni 模型。

`--mode`、`--duration`、`--resolution` 和 `--ratio` 必须同时满足该模型能力和创建 Schema。文本生成不传参考；首帧生成仅在对应模式实际支持时传 `--image-ref <Video Artifact ID>=first_frame`。参考数量、格式和大小以实际能力、导入与预检结果为准，不套用 H3 的混合引用限额。

## 2. 报价与错误

已授权的预算上限通过 `--budget-limit` 传递，已有账号通过 `--channel-account-id` 选择；实际 Schema 缺字段就报告部署差异，不删去预算重试。预检 `can_submit=false` 时保留原因并停止。

`model_retired`、`authorization_required`、`limits_unavailable` 等安全业务错误保留原码、原因、来源与可重试性。查询恢复沿原 Video task ID；超时或受理未知不能换模型重建任务。
