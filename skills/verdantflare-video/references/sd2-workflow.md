# SD2当前入口与历史兼容边界

仅用户指定SD2或恢复已有SD2任务时读取。新任务仍通过Studio统一Video MCP进入；宿主未声明SD2接入时报告该模型的能力缺口，不把旧公共API脚本当成自动备用渠道。

## 当前任务

1. 发现当前Studio工具和模型/渠道声明，确认SD2的业务标识、参数、项目引用方式与可用性。不能照搬H3的 `minimax-h3-ref2va`、参考数量或时间限制。
2. 输入通过当前Project/Artifact及宿主适配保存，固定Prompt、引用和幂等键后再创建任务。供应商路由与凭据由服务管理，Skill不持有S3或供应商密钥。
3. 已有任务沿原服务记录的模型、渠道、Task ID和提交ID恢复。只有当前宿主明确支持的恢复工具才可调用；缺少历史适配时保留原记录并报告，不重放生成。

## 附带旧客户端的含义

现有 `scripts/video_client.py` 保留了公共 `/v1/videos`、模型 `verdantflare-sd2`、平台API Key/S3配置、上传清理及本地Submission记录。这是历史实现，不证明当前Studio网关开放了同样的接口或规格。

旧 `install-config-macos.sh` / `install-config-windows.ps1` 会获取历史配置，部分流程还下载 `mc`；它们不是Studio认证修复入口，不因缺少旧API/S3变量而自动运行。历史参数与字段留在 [旧API说明](api.md)，只用于识别已有记录、迁移和维护旧客户端。

CLI `generate` 已默认使用H3的Studio MCP分支；指定SD2时提示使用宿主MCP工具，不会进入历史公共API/S3生成。也不把原公共API的720P、时长和文件数量限制宣称为所有部署的现行能力。

## 历史状态的解释与恢复

保留原始 `PREPARED`、`SENDING`、`CONFIRMED`、`REJECTED`、`UNKNOWN`。`UNKNOWN` 不等于失败，不能重新POST；`CONFIRMED` 才有已确认Task ID。原 `client_request_id` / Idempotency-Key保持不变，不因为换电脑重新生成。

历史 `queued` / `in_progress` 表示进行中；`completed` 仍须核对结果引用与文件，`failed` / `failure` 为终态。恢复适配必须保留这些状态语义，不能仅凭本地文件名猜任务完成。

查询、下载和临时对象保留规则依原任务契约执行，恢复不隐含授权清理其他对象或重新上传。不要输出供应商ID、S3对象键、签名URL或凭据；不能把旧任务转成新的收费任务来代替恢复。
