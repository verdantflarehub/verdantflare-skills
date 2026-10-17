---
name: verdantflare-artifact
description: Upload, read, download and verify immutable content versions through VerdantFlare Studio Artifact MCP. Use for controlled file transfer, ContentRef integrity and interrupted uploads; not for generic local file editing or build artifact management.
metadata:
  version: "0.1.0"
---

# VerdantFlare Artifact

已绑定 Studio 项目中的创作、素材修改或查询，自动完成必要上传或下载，沿用真实 project_id；写入任务继续完成 Project 提交、业务关联与回读，用户无需额外提示传输或检查。明确仅本地、只讨论或只上传时遵循其范围。响应未知先对账恢复，不重复收费生成；同步不自动批准、选用或发布 World。

## 1. 入口与范围

通过宿主 Studio `/mcp` 发现 `artifact.write`、`artifact.read`，按当前 Schema 调用。
使用 `STUDIO_MCP_URL`、`STUDIO_MCP_BEARER_TOKEN`；Bearer 由服务端解析真实身份，不额外登录或提交用户/组织头。

Artifact 管内容版本；上传成功不等于已加入 Project。
明确只上传时返回 ContentRef；已绑定项目的创作任务自动交接 Project 完成归档及角色关联，不等待用户再说“加入项目”。生成服务原生 Artifact 不能直接当中央 ContentRef。

## 2. 写入

1. 确认明确文件、用途和获授权 project_id。外部来源使用 user_edit 或 user_import，不使用假项目，也不冒充 task_output、legacy_import、asset_manifest。
2. 核对 MIME、实际字节数及 SHA-256；只复用已知且有权读取的固定版本，不声称有全库哈希搜索。
3. UTF-8 小文本可用 mode=text，最多 1 MiB 字节。二进制或大文本按[传输与恢复](references/transfer-and-recovery.md)执行 prepare、受控 PUT、commit。
4. 校验返回版本，按任务需要读回内容。交付包含完整 ContentRef、MIME、大小、SHA-256 和验证状态。

write_id 及声明在发送前固定，不含凭据。内容变化用新版本；旧版本与原始文件不覆盖。

## 3. 读取与下载

用固定 ContentRef 调用 metadata/text/download。访问路径若采用项目或资产，必须带完整的修订或版本对；引用与下载路径不是令牌。
下载再次授权，写入明确本地目标，完整校验后才形成最终文件，已有不同文件不覆盖。

传输只走同一 Studio 的受控路径，拒绝重定向、跨域凭据转发和任意 URL 代理。
不向 S3 直写，不索取 S3、下游服务或供应商凭据。

## 4. 恢复与交接

响应未知按原 write_id/upload_id 恢复；prepared 不代表字节完整，未提交上传可完整重传，不能宣传分块断点续传。
本地文件变更或摘要不符时停止原上传，保留记录并核对，不复用原 ID 改声明。

已绑定项目的创作成果默认将服务返回的 ContentRef 交给 Project 提交并回读，角色展示及查询需要的关联由 Project 流程核验。查询时从最新项目取得固定引用，按需下载并向用户展示，不要求用户手动下载；查询不产生业务写入。明确删除候选时交给 Project 清理该候选及引用，不在 Artifact 层物理删除共享字节。
`studio-workspace import/save-files` 会同时提交项目，不能用来执行“只上传”任务。
保留、释放、删除和跨环境迁移不属于隐含收尾操作。
