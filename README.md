# VerdantFlare Skills

本文描述工作区 Skill 包。每个目录都包含可被 Codex 发现和安装的 `SKILL.md` 与 `agents/openai.yaml`；工作区新增能力仍属于研发版，未自动发布到 Center Catalog 或安装到 Studio Runtime。下方版本标签是既有发布入口。当前可用技能还包括 [30 秒 MV](skills/verdantflare-music-mv/SKILL.md)。

## 工作区新增研发技能

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

Skill 通过 VerdantFlare Station 提供的 Music MCP 工具执行生成、分轨、音色训练与转换、已知歌词强制对齐、混音母带。Music3 候选使用最大生成时长作为上限并保留自然结尾，实际时长在审核点记录。音频、真人录音和人声模型不进入 Git。

男女对唱制作采用逐句声部计划与独立轨校验；已有获认可的自然演唱对唱可按时间轴替换单一歌手，并对照原试听检查伴奏、电平和声部。机器检查通过不代表歌词、音色或最终音质通过。

最终 MP3 使用 `<创作者显示名>-<歌曲名>.mp3` 命名，例如 `Creator-Demo.mp3`。

## verdantflare-video

`verdantflare-video` 是兼容 macOS 和 Windows 的 Codex Skill，通过一个 Video MCP 统一支持模型与渠道选择，未指定时使用宿主默认模型与渠道（当前模型为 H3、渠道为 H3-VDN），也支持明确指定模型简称（H3、SD2）及已声明的推理渠道。fal 已作为 H3 Reference-to-Video 的显式渠道开放；调用固定传入当前 `project_id` 与 `route=fal`，是否可用以宿主 MCP 能力声明为准，且不会失败回退。

### 安装命令

当前版本：`verdantflare-video-v0.3.1`

在 Codex 中执行：

```text
使用 $skill-installer 从 https://github.com/verdantflarehub/verdantflare-skills/tree/verdantflare-video-v0.3.1/skills/verdantflare-video 安装 Skill。
```

### 配置命令

macOS：

```bash
bash "$HOME/.codex/skills/verdantflare-video/scripts/install-config-macos.sh"
```

Windows PowerShell：

```powershell
& "$HOME\.codex\skills\verdantflare-video\scripts\install-config-windows.ps1"
```

本机需要 Python `3.10` 或更高版本。配置脚本会从内置的 VerdantFlare 引导地址自动下载配置。

配置必须包含以下三项：

```dotenv
VERDANTFLARE_VIDEO_API_KEY=<required>
VERDANTFLARE_VIDEO_S3_ACCESS_KEY=<required>
VERDANTFLARE_VIDEO_S3_SECRET_KEY=<required>
```

显式使用 fal 渠道时，命令行脚本通过统一 MCP 网关调用，还需要：

```dotenv
# 统一通过 Studio MCP 网关调用 (5090 集群: https://studio.dev.verdantflarehub.com/mcp)
STUDIO_MCP_URL=https://studio.dev.verdantflarehub.com/mcp
STUDIO_MCP_BEARER_TOKEN=<required>
```

供应商 `FAL_KEY` 只配置在 Video MCP Server，不能放入 Skill 配置。可用统一客户端
`python3 scripts/video_client.py check` 检查 MCP 工具，再通过 `generate`、
`status`、`result` 或 `resume` 执行和恢复 fal Ref2VA 任务。

### 使用 Skill

```text
使用 $verdantflare-video，明确采用 SD2，根据 ~/Desktop/product.png 生成一个 9:16、10 秒的产品广告视频。
```

## verdantflare-image

`verdantflare-image` 是专注于原子图像资产生成、以图生图与局部重绘的领域级 Skill。它负责将上层视觉意图（如 MV 人物设计胸部四视图、服装无脸人台三视图、分镜单格图 F01~F08、纯场景设计图）转化为技术受控的生图请求，默认采用 `codex`（`gpt-image-2.5-sunburst`，亦支持 `gpt-image-2.5-flare`）引擎驱动，亦支持 `gemini`（`gemini-3.1-flash-image`），通过 `image.*` MCP 工具与 REST 接口调度执行，最终将技术合格且校验 SHA-256 的不可变 `ImageCandidate` 资产受控归档到指定 `source/` 目录。

### 安装命令

当前版本：`verdantflare-image-v0.1.0`

在 Codex 中执行：

```text
使用 $skill-installer 从 https://github.com/verdantflarehub/verdantflare-skills/tree/dev/skills/verdantflare-image 安装 Skill。
```

同时在本地 Codex 注册 Studio 统一 MCP 服务：

```bash
codex mcp add verdantflare-studio \
  --url "${STUDIO_MCP_URL:-https://studio.dev.verdantflarehub.com/mcp}" \
  --bearer-token-env-var STUDIO_MCP_BEARER_TOKEN
```

### 使用 Skill

```text
使用 $verdantflare-image，为项目 creator/project-demo 生成 B01 单元的角色服装无脸人台三视图，采用 codex 引擎，画幅 16:9。
```

### 命令行客户端 (CLI)

技能随附纯 Python 标准库驱动工具 `scripts/image_client.py`，支持独立在终端执行生图、轮询与哈希校验下载（默认使用 `codex` 引擎）：

```bash
python3 skills/verdantflare-image/scripts/image_client.py generate \
  --prompt "科技风极简标志设计，绿色与深色背景" \
  --engine codex \
  --output /tmp/test-image.png
```

## 变更记录

详见 [`Changes.md`](Changes.md)。
