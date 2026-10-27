# Changes

## 参考声线与配乐检查工具 — 2026-10-17

`verdantflare-speech-v0.3.0`, `verdantflare-music-v0.5.0`。

- Speech 0.3.0 补充参考声线登记与实验性同文 WORLD 声谱融合工具，固定来源后可作为新参考生成不同文稿；保留候选、听审和稳定性边界。
- 增加新音色设计路由，区分参考波形克隆与参考特征指导的文字设计。VoxCPM2 候选运行器在 Speech 应用源码中准备，23 项单元测试通过，尚未通过 5090 GPU 验证或作为 Studio 工具开放。
- Music 0.5.0 配乐增加源长度、时间线覆盖、停乐冲突、BPM 和听审状态检查工具；17 项单元测试及 5090 dev 声谱融合回归通过。技能源码更新不代表客户端安装，配乐生成可用性仍由实时预检决定。

## 媒体执行流程六包发布 — 2026-10-17

`x-verdantflare-short-drama-v0.2.0`, `verdantflare-video-v0.4.2`, `verdantflare-music-v0.4.1`, `verdantflare-speech-v0.2.1`, `verdantflare-project-v0.1.0`, `verdantflare-artifact-v0.1.0`。

- 固化多版本宣传片的执行方案与模板：当前依据、故事因果、素材动作盘点、生成／剪辑映射、依赖和授权延续；不新增服务Schema或固定审批数量。
- Music增加画面驱动的器乐配乐分支；Speech增加旁白音色／表演及时间窗口交接；Video增加叙事动作与可用选段验收。
- Project／Artifact按原提交与不可变版本恢复未验证文件，区分项目提交和字节回读。
- 六包提供独立ZIP和SHA-256校验，修复Music独立安装的外部引用；服务能力与创作听审另行核验。

## verdantflare-video-v0.4.0 / Higgsfield 插件 0.1.2 — 2026-10-14

- 发布 Seedance 2.5 Draft × Higgsfield R2V 入口：480p、4–15 秒、多图与视频引用、完整素材报价与原任务恢复。
- Higgsfield 插件可定位独立安装的 Video Skill，不再要求成员拥有开发源码工作区；保留显式源码路径兼容。
- 更新成员安装入口、模型说明及独立安装文档引用；复用既有真实生成证据，不新增收费测试。

## x-verdantflare-short-drama-v0.1.0 — 2026-10-11

- 首次发布统一短剧 Skill，保留作者标记 `x-`；原七项能力收拢为一个入口和按需模块。
- 完整剧本必交正文、人物小传、地点与场次、道具、服装 Look、逐镜连续性，配套版本索引与审核返修记录。
- 提供规范注释、交付模板、迁移说明、导演计划校验器与本机来源锚定拆解脚本。
- 压缩包只包含该 Skill；媒体依赖文档使用固定源码链接，避免独立安装后出现兄弟目录断链。
- GitHub 标签/Release 与独立压缩包发布，不代表 Center Catalog 上架或客户端已安装。

## 未发布

- `verdantflare-image` 同步 Google 渠道（`engine="gemini"`）Nano Banana 全系列模型目录与 [Google 图像调用参考](skills/verdantflare-image/references/google.md)：明确展示名（`Nano Banana 2.1` / `Nano Banana Pro` / `Nano Banana 2` / `Nano Banana 2 Lite` / `Nano Banana`）与请求 `model_id`（默认 `gemini-nano-banana-2.1`，以及 `gemini-3-pro-image`、`gemini-3.1-flash-image`、`gemini-3.1-flash-lite-image`、`gemini-2.5-flash-image`）的映射、1–6 张参考图编辑与 7 种画幅比例（`1:1`、`2:3`、`3:2`、`3:4`、`4:3`、`9:16`、`16:9`）约束。




- 短剧 0.1.1 研发源码新增对白交接入口，以固定提交关联 Speech 技能；补齐表演模块的声线、逐句恢复与项目归档路由。

- 新增 `verdantflare-speech` 0.1.0 研发入口，补齐短录制文稿、参考接收、版本绑定、逐句输入、未知任务恢复和候选归档。
- 区分已验证的 IndexTTS2 内部 API 与尚未开放的 Studio Speech 工具；缺工具时继续独立准备，不引入内部凭据或直连路径。

- 补齐短剧场景、复杂道具与每镜 Blender 白模交付；完善人物参考职责、叙事机位和跨镜连续性。
- AI 短剧及 Project、Artifact、Blender、Image、Video、Music、Music MV 入口统一阶段成果同步到原 Studio 项目；保留候选、修改、固定引用和恢复记录，不自动批准或发布 World。
- 本项是源码规则更新；已安装包和在线 MCP 的生效状态分别核验，不据此宣称已部署。

- 新增 `verdantflare-project`、`verdantflare-artifact`、`verdantflare-blender`，各自包含完整元数据和按需操作参考，可独立目录安装。
- 复用 Studio workspace 客户端；明确仅 Bearer 身份、固定修订、上传幂等、Blender 实例路由与 text JSON 响应、场景保存和工程归档边界。
- 直接从源码验证元数据和包内引用，完成工作副本、Blender 服务回归及 5090 真实内容/工程闭环；未安装技能或发布技能标签。详细证据见主工作区技能验收计划。
- 补充 Blender 0.1.5 首次已有工程恢复、只读初始化限制及未知状态处理；服务修复的发布状态独立记录，不与技能安装混写。

- 修正 Image / Video Skill、README 与环境说明中的旧工具名、固定渠道默认值和认证前提，以当前 Studio 工具发现和真实主体会话为入口。
- 明确中央 ContentRef 与媒体服务原生 Artifact 的适配边界、Project 归档及 Markdown Preview 展示方式；按实际输出核验图像尺寸。
- 标注 Image REST 客户端、Video 旧 SD2 / fal 分支与当前 Studio 的兼容限制，保留历史 API 契约供旧任务识别和恢复。
- 本项仅更新文档与技能元数据，未迁移 CLI 实现、发布安装包或部署服务。

## verdantflare-image-v0.1.0

- 新增规范的 `verdantflare-image` Skill，全面废除旧 `verdantflare-image-codex`。
- 完整接入 VerdantFlare Image MCP 服务（`artifact.import`、`image.generate`、`image.edit`、`image.inpaint`、`image.status`、`image.result`、`image.list`）。
- 支持 `codex`（`gpt-image-2`）与 `gemini`（`gemini-3.1-flash-image`）双核 API 引擎。
- 增加人物设计四视图、服装无脸人台三视图、分镜单格画面的视觉规格文档与异步生成工作流。
- 增加零第三方依赖的 Python 客户端工具 `scripts/image_client.py`，支持终端直接生图、轮询等待与不可变 SHA-256 校验。
- 增加全面的自动化单元测试套件 `tests/test_verdantflare_image.py`。

## verdantflare-music-v0.4.0

- 增加男女对唱的逐句声部计划、自然演唱源单声部换声、源轨覆盖与混音电平校验流程。
- 增加伴奏校准、对唱装配与验证脚本；区分试听角色窗口轨和可独立重混的纯净分轨。
- 根据《越过噪点》v7/v18 实际对照记录已验证的能力边界：Mengsk 男声换声试听可用，音质仍需继续优化，不将机器检查视为最终听审。
- 完善局部重绘、换声保真、制作前预检及当前曲风规范，并保持真实 MCP 能力与审核流程一致。

## verdantflare-music-v0.3.1

- 最终 MP3 改为 `<创作者显示名>-<歌曲名>.mp3`，不再保留 `Final_Song.mp3` 兼容文件。

## verdantflare-music-v0.3.0

- 新增显式 `lyrics.align` 歌词强制对齐阶段，以转换后干声和已批准逐行歌词生成 `Aligned_Lyrics.lrc` Artifact。
- 对齐结果必须保持行数和逐行文字，时间戳严格递增且不超过节目时长。
- 禁止用原曲 LRC、线性缩放或手工伪造时间戳作为对齐 fallback。

## verdantflare-music-v0.2.0

- 将 Music3 时长语义从“精确固定时长”改为“最大生成时长”，保留模型自然结束的候选。
- 审核点 2 分别记录两个候选的实际时长，不再因为候选未填满上限而失败或反复抽取 seed。
- 用户要求精确节目时长时明确报告当前生成链路不保证该约束，不用裁切或静音伪造结果。

## verdantflare-music-v0.1.0

- 新增从一句音乐灵感到最终母带交付的 `verdantflare-music` Skill。
- 固化双候选生成、UVR5 分轨、RVC 音色训练或复用、音色转换、歌词对齐和混音母带流程。
- 增加五个人工审核门禁、精确时长约束、标准交付物和可恢复审核记录规范。
- 输入收敛为一句音乐灵感，以及原始人声 MP3 或已批准的人声模型。
- 原始人声和模型按创作者统一管理，歌曲交付物按 `<创作者>/<项目>` 管理并引用可复用模型。
- 统一使用当前工作区的 `.output/music/` 作为人声资产和歌曲项目的输出根目录。
- Skill 只负责制作计划与工具选择，不包含 Kubernetes 运维、音频算法或 Station 任务资产管理。

## verdantflare-video-v0.3.1

- 配置安装脚本改为从内置 VerdantFlare 引导地址自动下载配置，无需再通过终端手工输入一次性配置地址。
- `SKILL.md` 已完整翻译为中文，并保留命令、参数、API 字段和状态值原样。
- 增加默认引导地址的安装回归测试。

## verdantflare-video-v0.3.0

- Linux runtime 会优先读取 Linux 配置，并在 WSL 中搜索已挂载的 Windows 配置：`/mnt/<drive>/Users/<user>/AppData/Local/VerdantFlare/Video/.env`。
- 找不到配置时会输出实际搜索路径；发现多个配置时会提示冲突路径。
- 本地素材上传失败时会明确说明 bucket 不存在、未 provision 或无权限。
- bucket 错误发生在视频 `POST` 之前，不代表图片或 API Key 有问题；纯文本请求不经过素材上传。

## verdantflare-video-v0.2.0

- 增加 macOS 和 Windows 支持。
- 增加 Windows PowerShell 配置入口和 Windows `mc.exe` 运行依赖。
- 增加 Windows 配置目录和任务锁兼容处理。

## 发布规则

- `skills/verdantflare-video` 内容变化时发布新的 Skill 版本和不可变 Tag。
- `skills/verdantflare-music` 内容变化时发布新的 Skill 版本和不可变 Tag。
- 仅修改外部指南 `README.md` 或 `Changes.md` 时不升级 Skill 版本。
