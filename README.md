# VerdantFlare Skills

本文描述工作区 Skill 包。每个目录都包含可被 Codex 发现和安装的 `SKILL.md` 与 `agents/openai.yaml`；工作区新增能力仍属于研发版，未自动发布到 Center Catalog 或安装到 Studio Runtime。下方版本标签是既有发布入口。当前可用技能还包括 [30 秒 MV](skills/verdantflare-music-mv/SKILL.md)。

## 工作区新增研发技能

Project、Artifact、Blender 三项基础技能已实现并完成本地及 5090 真实调用验证，当前为未发布开发版：

- [`verdantflare-project`](skills/verdantflare-project/SKILL.md)：项目创建、固定修订、工作副本保存与恢复。
- [`verdantflare-artifact`](skills/verdantflare-artifact/SKILL.md)：内容上传、受控下载、摘要校验与上传恢复。
- [`verdantflare-blender`](skills/verdantflare-blender/SKILL.md)：指定实例的场景操作、幂等恢复与 Project 工程归档。

三项目录各自包含完整引用资料。本轮直接读取仓库源码验证，不安装到本地客户端。Project/Artifact 的本地工作副本操作复用 Studio 0.5.50 的 `studio-workspace`；宿主 MCP 仍是首选入口。
Blender 0.1.5 补齐首次 edit 会话的工程恢复，并通过真实 Blender 隔离加载测试；第二主机恢复和完整桌面验收仍不包含在本轮通过范围内。

设计与测试事实由主工作区 `docs/design/skills/` 和 `plan/2026.10.skills-foundation.plan.md` 维护，未发布到 Center Catalog 或安装到 Studio Runtime。

以下五项已纳入 VF Skill 包，可从本仓库目录安装或直接在工作区开发验证：

- [`x-verdantflare-short-drama-director`](skills/x-verdantflare-short-drama-director/SKILL.md)：剧本、故事概念或广告 brief 到 treatment、Shot 计划和 Generation Unit 提案。
- [`x-verdantflare-short-drama-film-production`](skills/x-verdantflare-short-drama-film-production/SKILL.md)：影视段落的资产、镜头、视频候选和验片交接。
- [`x-verdantflare-short-drama-commercial-production`](skills/x-verdantflare-short-drama-commercial-production/SKILL.md)：品牌片、产品广告和电商短视频制作与声明审校。
- [`x-verdantflare-short-drama-character-performance`](skills/x-verdantflare-short-drama-character-performance/SKILL.md)：单镜角色动作、情绪、对白表演与动态验片。
- [`x-verdantflare-short-drama-local-scene-breakdown`](skills/x-verdantflare-short-drama-local-scene-breakdown/SKILL.md)：使用本机 Ollama 从单场剧本提取来源锚定事实和动作节拍。

本地安装时，将对应的 `skills/<name>` 目录交给 Skill Installer；正式发布前仍需完成版本、来源、授权和依赖登记。服务器能力继续通过现有 Image、Video、Music MCP 进入，不在这些 Skill 中伪造新的 MCP 接口。

## x-verdantflare-short-drama-director（开发版）

[`x-verdantflare-short-drama-director`](skills/x-verdantflare-short-drama-director/SKILL.md) 负责将剧本、故事概念或广告 brief 编成导演方案、Shot 时间线、资产需求和可选 Generation Unit 提案。它不调用生成模型；计划可用 `python3 skills/x-verdantflare-short-drama-director/scripts/validate_plan.py <plan.json>` 检查时码、引用与 H3 单元约束。设计边界见主工作区的 `docs/design/workflow/verdantflare-director-skill-v0.1.md` 草案。当前仅在仓库中完成本地实现和测试，未发布到 Center Catalog 或安装到 Studio Runtime。

## 制作技能（开发版）

- [`x-verdantflare-short-drama-film-production`](skills/x-verdantflare-short-drama-film-production/SKILL.md)：影视剧本、场景到资产、镜头、视频候选和验片交接。
- [`x-verdantflare-short-drama-commercial-production`](skills/x-verdantflare-short-drama-commercial-production/SKILL.md)：广告 brief、产品连续性、制作与声明审校。
- [`x-verdantflare-short-drama-character-performance`](skills/x-verdantflare-short-drama-character-performance/SKILL.md)：单镜角色动作、情绪、对白和动态验片。

三者按任务阶段调用已有短剧 Director、Image、Video 和 Music MV Skill，不另建生成接口。本地源代码和指导文档可供开发验证，未发布到 Center Catalog 或安装到 Studio Runtime；边界见主工作区 `docs/design/workflow/verdantflare-production-skills-v0.1.md` 草案。

## x-verdantflare-short-drama-local-scene-breakdown（本机模型开发版）

[`x-verdantflare-short-drama-local-scene-breakdown`](skills/x-verdantflare-short-drama-local-scene-breakdown/SKILL.md) 使用本机 Ollama 已安装的 `gemma3:12b` 处理单场 UTF-8 剧本，输出带原文证据的事实、动作节拍、待审推断及拒收清单。直接运行 `python3 skills/x-verdantflare-short-drama-local-scene-breakdown/scripts/breakdown.py --input <scene.txt> --output <breakdown.json>`；不自动拉取模型、不连接远端、不覆盖已有输出。草案边界见主工作区 `docs/design/workflow/verdantflare-local-scene-breakdown-v0.1.md`。

## verdantflare-music

`verdantflare-music` 是从一句灵感推进到最终母带交付的 Codex Skill。它负责编制词曲企划、调用 VerdantFlare Music 制作工具，并通过五个人工审核点管理候选、分轨、个人音色、音色转换和母带结果。

输入是一句音乐灵感，以及原始人声 MP3 或一个已批准的人声模型。原始人声和训练模型按创作者管理并可跨歌曲复用，歌曲交付物统一放在当前工作区的 `.output/music/<创作者>/<项目>/`。

### 安装命令

当前版本：`verdantflare-music-v0.4.0`

在 Codex 中执行：

```text
使用 $skill-installer 从 https://github.com/verdantflarehub/verdantflare-skills/tree/verdantflare-music-v0.4.0/skills/verdantflare-music 安装 Skill。
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

已发布版本：`verdantflare-video-v0.3.1`。固定标签不包含本地未发布更新。

```text
使用 $skill-installer 从 https://github.com/verdantflarehub/verdantflare-skills/tree/verdantflare-video-v0.3.1/skills/verdantflare-video 安装 Skill。
```

```text
使用 $verdantflare-video，在当前Studio项目中制作一个16:9、15秒的H3视频。先检查宿主渠道与参考素材是否可用，再按确认的输入提交；已有任务按原ID恢复。
```

### 客户端兼容状态

`scripts/video_client.py` 尚有两条旧路径：仅显式 `--model minimax-h3-ref2va --route fal` 进入MCP生成分支，且要求 `video.generate` / `artifact.import`；省略这些参数可能进入旧SD2公共API及S3上传分支。它目前不是所有Studio部署的通用生成入口。其 `check` 失败应结合实际工具列表判断，不能直接认定Video服务不可用。

新任务优先使用宿主实际注册的Video工具。历史SD2安装脚本、API Key和S3配置仅用于识别旧安装，不再列为Studio流程的必填条件；不要为了修复Studio认证运行旧配置安装器。已有SD2任务的恢复边界见 [SD2说明](skills/verdantflare-video/references/sd2-workflow.md)。

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
