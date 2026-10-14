# 上游来源与适配

## 1. 固定来源

- [Higgsfield Skills 0.13.0](https://github.com/higgsfield-ai/skills/tree/f83af0bc1d937c8119099a11f8ebbf5e6fb99819)：higgsfield-generate 的视频模型发现、生成和恢复流程。
- [Codex 插件打包](https://developers.openai.com/plugins/build/plugins)：兼容 manifest 与本地 marketplace 格式。
- [MIT 许可](LICENSE)：保留上游版权声明。

## 2. 修改范围

团队适配版本 0.1.0。保留视频意图与模型选择流程，将全局 CLI 安装、本地会员登录、同步等待和公开供应商结果替换为自有 Video Skill、Studio 异步任务与中央 Artifact。预算以用户授权为准。其他官方功能不在此包中；不宣称未修改的官方插件命令全部兼容。

## 3. 本地安装边界

本仓库 marketplace 的 source.path 仅指向本插件目录。缓存安装时配置 VERDANTFLARE_SKILLS_ROOT 指向用户自有 Skills 仓库。OAuth 与二进制位于包根目录之外，不打包运行时。宿主发现、实际安装和新会话加载须分别验证。
