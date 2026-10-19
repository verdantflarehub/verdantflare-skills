# VerdantFlare Skills

本文描述工作区 Skill 包。每个目录都包含可被 Codex 发现和安装的 `SKILL.md` 与 `agents/openai.yaml`；工作区新增能力仍属于研发版，未自动发布到 Center Catalog 或安装到 Studio Runtime。下方版本标签是既有发布入口。当前可用技能还包括 [30 秒 MV](skills/verdantflare-music-mv/SKILL.md)。

## 本轮媒体技能发布（2026-10-17）

各技能使用独立不可变标签，Release附独立ZIP和SHA256SUMS。安装到当前客户端后在下一回合加载；发布不代表Center Catalog或Studio Runtime已安装，也不代替媒体听审。

| 技能 | 固定版本 |
| --- | --- |
| `x-verdantflare-short-drama` | [0.2.0](https://github.com/verdantflarehub/verdantflare-skills/releases/tag/x-verdantflare-short-drama-v0.2.0) |
| `verdantflare-video` | [0.4.2](https://github.com/verdantflarehub/verdantflare-skills/releases/tag/verdantflare-video-v0.4.2) |
| `verdantflare-music` | [0.6.0](https://github.com/verdantflarehub/verdantflare-skills/releases/tag/verdantflare-music-v0.6.0) |
| `verdantflare-speech` | [0.3.0](https://github.com/verdantflarehub/verdantflare-skills/releases/tag/verdantflare-speech-v0.3.0) |
| `verdantflare-project` | [0.1.0](https://github.com/verdantflarehub/verdantflare-skills/releases/tag/verdantflare-project-v0.1.0) |
| `verdantflare-artifact` | [0.1.0](https://github.com/verdantflarehub/verdantflare-skills/releases/tag/verdantflare-artifact-v0.1.0) |

使用 `$skill-installer` 从对应标签的 `skills/<技能名称>` 安装。已有目录先备份再升级；不覆盖个人配置或项目素材。Speech 0.3.0 包含参考登记、实验性声谱融合及音色设计路由；Music 0.5.0 包含配乐计划检查。VoxCPM2 尚未完成 GPU 验证或开放 Studio 调用，音乐生成以实时预检为准。

## 工作区新增研发技能

Project、Artifact、Blender 三项基础技能已实现并完成本地及 5090 真实调用验证。Project与Artifact本轮首次发布0.1.0；Blender仍为未发布开发版：

- [`verdantflare-project`](skills/verdantflare-project/SKILL.md)：项目创建、固定修订、工作副本保存与恢复。
- [`verdantflare-artifact`](skills/verdantflare-artifact/SKILL.md)：内容上传、受控下载、摘要校验与上传恢复。
- [`verdantflare-blender`](skills/verdantflare-blender/SKILL.md)：指定实例的场景操作、幂等恢复与 Project 工程归档。

三项目录各自包含完整引用资料。此前源码联调未安装客户端；本轮六包的发布与安装入口见首节。Project/Artifact 的本地工作副本操作复用 Studio 0.5.50 的 `studio-workspace`；宿主 MCP 仍是首选入口。
Blender 0.1.5 的首次工程恢复已通过干净工作副本、真实内容服务、后台 Blender 编辑保存及重新打开验证。Project/Artifact 已完成 Windows↔Linux 往返；第二台物理 Blender 主机和完整桌面验收仍未覆盖。

设计与测试事实由主工作区 `docs/design/skills/` 和 `plan/2026.10.skills-foundation.plan.md` 维护，未发布到 Center Catalog 或安装到 Studio Runtime。

对白入口：[`verdantflare-speech`](skills/verdantflare-speech/SKILL.md) 0.3.0，通过 Studio 生成对白／旁白，复用固定参考声线，恢复原任务并归档。Speech 0.1.1、IndexTTS2 0.1.3 和原生 Artifact／Project 交接已完成真实旁白验证；执行时仍发现实际工具和 Schema。源码更新不代表客户端已安装。

短剧相关能力统一为一个研发 Skill：

- [`x-verdantflare-short-drama`](skills/x-verdantflare-short-drama/SKILL.md)：编剧、审核、导演、制作，按需进入表演、本机拆解与广告适配模块。
- 完整剧本交付六类独立文档，见[规范](skills/x-verdantflare-short-drama/references/writing/script-package-standard.md)与[模板](skills/x-verdantflare-short-drama/references/writing/delivery-templates.md)。
- 本轮发布新增[多版本媒体执行方案](skills/x-verdantflare-short-drama/references/production/execution-planning.md)与[编写模板](skills/x-verdantflare-short-drama/references/production/execution-plan-template.md)，用于宣传片的要求收敛、分批生成、素材盘点与粗剪交接。
- 旧目录与命令迁移见[迁移说明](skills/x-verdantflare-short-drama/references/migration.md)。仅保留一个可发现入口，媒体调用继续由 Image / Video / Music Skill 执行。

已发布 GitHub 版本 `x-verdantflare-short-drama-v0.2.0`，包含独立安装包与 SHA-256 校验文件；未发布到 Center Catalog，已安装客户端不会自动更新。

安装命令：

```text
使用 $skill-installer 从 https://github.com/verdantflarehub/verdantflare-skills/tree/x-verdantflare-short-drama-v0.2.0/skills/x-verdantflare-short-drama 安装 Skill。
```

## verdantflare-music

0.6.0 增加现代日系电子流行制作分支、中文歌词与编曲审核，以及通过实际能力发现使用独立修音、人声效果、合成器与预混。参考资料学习不等同逐曲听审，当前服务能力由 Studio Schema 决定。

`verdantflare-music` 是从一句灵感推进到最终母带交付的 Codex Skill。它负责编制词曲企划、调用 VerdantFlare Music 制作工具，并通过五个人工审核点管理候选、分轨、个人音色、音色转换和母带结果。

本轮发布的[影视器乐配乐分支](skills/verdantflare-music/references/picture-scoring.md)按画面、旁白与实测长度编排，不套用完整歌曲的人声制作流程。

输入是一句音乐灵感，以及原始人声 MP3 或一个已批准的人声模型。原始人声和训练模型按创作者管理并可跨歌曲复用，歌曲交付物统一放在当前工作区的 `.output/music/<创作者>/<项目>/`。

### 安装命令

当前版本：`verdantflare-music-v0.6.0`

在 Codex 中执行：

```text
使用 $skill-installer 从 https://github.com/verdantflarehub/verdantflare-skills/tree/verdantflare-music-v0.6.0/skills/verdantflare-music 安装 Skill。
```

### 使用 Skill

```text
使用 $verdantflare-music，创作一首 最长 200 秒、黑暗电影感的中文叙事歌曲，并在每个审核点等我确认。
```

Skill 通过 Studio 统一网关提供的 Music MCP 工具执行生成、分轨、音色训练与转换、已知歌词强制对齐、混音母带。Music3 候选使用最大生成时长作为上限并保留自然结尾，实际时长在审核点记录。音频、真人录音和人声模型不进入 Git。

男女对唱制作采用逐句声部计划与独立轨校验；已有获认可的自然演唱对唱可按时间轴替换单一歌手，并对照原试听检查伴奏、电平和声部。机器检查通过不代表歌词、音色或最终音质通过。

最终 MP3 使用 `<创作者显示名>-<歌曲名>.mp3` 命名，例如 `Creator-Demo.mp3`。

## 媒体技能的共同接入方式

Image、Video等业务MCP统一经Studio进入。先使用真实用户会话发现工具，再创建或打开Project、保存素材并执行生成。`project_id` 必须来自服务，不使用 `default` 或目录式别名。

```dotenv
STUDIO_MCP_URL=https://studio.example.com/mcp
STUDIO_MCP_BEARER_TOKEN=<绑定真实Core主体的会话令牌>
```

现行 Bearer 本身代表用户身份，由 Core 校验归属、有效期与撤销状态，客户端无需额外登录。遇到工具缺失或403，核对服务端身份绑定、工具注册及项目权限，不用登录步骤或自报用户头绕过。浏览器Cookie与CLI Bearer各用对应认证方式，不能互换令牌值。

素材通过Project/Artifact受控保存。生成服务的原生Artifact与中央 `ContentRef` 按宿主适配流程转换；上传成功不等于任意生成服务已经能消费该引用。当前流程不要求客户端持有S3密钥或供应商密钥。

配置与兼容边界见 [环境说明](skills/ENVIRONMENT.md)，工作副本与恢复见 [Project/World客户端](skills/_shared/project-world-client.md)。发布标签表示既有版本，本地文档修改不代表安装包或服务已经更新。

## verdantflare-video

[`verdantflare-video`](skills/verdantflare-video/SKILL.md) 通过Studio选择宿主已接入的视频模型和渠道，执行生成、查询、恢复与下载。用户未指定时解析当前宿主默认值并确认可用性；不把H3-VDN、Sol或fal写成跨环境固定默认，也不静默换渠道。

H3 Ref2VA业务模型为 `minimax-h3-ref2va`。现有网关创建工具为 `video.create`，配套 `video.status`、`video.result`；以本次真实会话发现的注册名和参数为准。fal是否配置、时长和画幅是否支持，需要分别核实，不能只看工具Schema的默认值。

### 安装与使用

已发布版本：`verdantflare-video-v0.4.2`。固定标签不包含本地未发布更新。

```text
使用 $skill-installer 从 https://github.com/verdantflarehub/verdantflare-skills/tree/verdantflare-video-v0.4.2/skills/verdantflare-video 安装 Skill。
```

```text
使用 $verdantflare-video，在当前Studio项目中制作一个16:9、15秒的H3视频。先检查宿主渠道与参考素材是否可用，再按确认的输入提交；已有任务按原ID恢复。
```

### Seedance 2.5 Draft × Higgsfield

Video v0.4.0 包含已上线 Draft R2V 的客户端支持。使用个人 Studio MCP Token，无需成员登录 Higgsfield 或安装官方 CLI；服务端保管会员凭据。需要可用团队额度，额度不足时由管理员在独立 Higgsfield 应用调整。

Video v0.4.1 补齐管理员授权交接与停服升级互斥，复用原 Service 的发布标记；结果未知按原 pending 对账，不重传凭据。维护管理员需更新到该版本，已有成员的生成和恢复参数保持。

```text
使用 $verdantflare-video，以 Seedance 2.5 Draft × Higgsfield，使用当前项目的角色参考图和动作参考视频生成 4 秒、480p 的 R2V。按完整素材报价执行，已有任务按原 ID 恢复。
```

支持 `model=seedance_2_5_draft`、`route=higgsfield`、4–15 秒、480p、最多 4 图＋1 视频。通过 `--image-ref`／`--video-ref` 传入已导入的 Artifact，不使用首尾帧模式。

Higgsfield 专用插件现为 **0.1.2**，在本仓库 marketplace 更新；依赖上述 Video v0.4.0。插件自动定位独立安装的 Video Skill，也兼容自有仓库配置。已安装成员需更新技能／插件并在新会话加载；源码发布不代表其他成员客户端已自动更新。

### 客户端兼容状态

当前 `scripts/video_client.py` 的新生成统一通过 Studio 工具发现、能力检查及 `video.preflight/create` 提交，恢复保持原模型、渠道和任务 ID。历史 SD2 只保留恢复兼容；不要为 Studio 认证运行旧配置安装器或安装 S3 客户端。当前配置使用 `STUDIO_MCP_URL` 和 `STUDIO_MCP_BEARER_TOKEN`。

## verdantflare-image

[`verdantflare-image`](skills/verdantflare-image/SKILL.md) 生成、编辑与局部重绘图像候选。默认采用 `codex` / `gpt-image-2.5-sunburst`，也可按用户选择和宿主能力使用其他已接入模型。输出须验证实际尺寸、字节数及SHA-256，再归档到当前Project；技术成功不代替创作审核。

### 安装与使用

既有版本记录：`verdantflare-image-v0.1.0`。以下 `dev` 入口用于获取当前开发版，不是不可变发布标签。

```text
使用 $skill-installer 从 https://github.com/verdantflarehub/verdantflare-skills/tree/dev/skills/verdantflare-image 安装 Skill。
```

注册Studio统一网关时，从环境读取已绑定真实主体的会话令牌：

```bash
codex mcp add verdantflare-studio \
  --url "$STUDIO_MCP_URL" \
  --bearer-token-env-var STUDIO_MCP_BEARER_TOKEN
```

```text
使用 $verdantflare-image，在当前已打开的Studio项目中，依据已导入的身份照片制作角色三视图候选；保留原图，生成后在Markdown Preview内嵌展示实际图片。
```

没有打开项目时，通过 `project.create/open` 获取服务ID。现有网关提供 `image.create/edit/inpaint/status/result`；历史 `image.generate`、`image.list` 仅在实际注册时使用。

### 客户端兼容状态

`scripts/image_client.py` 仍按REST根地址拼接 `/api/tasks` 等路径。虽然它读取 `STUDIO_MCP_URL`，但这不表示已经适配Studio `/mcp` JSON-RPC、Cookie登录或中央Artifact传输。当前优先使用宿主Image工具；不要把 `/mcp` 地址直接代入旧CLI，也不要改成下游独立公网地址。

CLI现状见 [Image CLI](skills/verdantflare-image/references/cli.md)，实际调用与原生Artifact转换见 [Image调用说明](skills/verdantflare-image/references/contracts.md)。

## 变更记录

详见 [`Changes.md`](Changes.md)。
