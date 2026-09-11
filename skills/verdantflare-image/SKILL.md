---
name: verdantflare-image
description: 通过 VerdantFlare Image MCP 服务执行领域级原子图像资产生成、以图生图与局部重绘；当用户或上层编排 (如 verdantflare-music-mv) 需要制作人物设计四视图 (01-front-neutral/02-front-smile/03-left-15deg/04-right-15deg)、服装无脸人台三视图 (05-wardrobe-turnaround)、分镜单格画面 (F01~F08)、纯场景概念图，或执行遮罩局部修复时使用。默认权威采用 codex 引擎 (gpt-image-2.5-sunburst)，亦支持 codex 极速探索模型 (gpt-image-2.5-flare) 与 gemini 引擎 (gemini-3.1-flash-image)，原生支持官方四段式提示词架构、透明背景 (background="transparent") 与不可变 SHA-256 校验归档。
---

# VerdantFlare 图像制作 (VerdantFlare Image)

专注于通过 VerdantFlare 领域级 Image MCP 服务，将上层编排的视觉意图可靠生产为技术合格、具有不可变哈希标识的 `ImageCandidate` 资产。

详细契约见 `references/contracts.md`，视觉规格与构图约束见 `references/visual-specs.md`，完整异步流转见 `references/workflow.md`。

---

## 1. 职责边界

- **原子资产生产**：专注于单张或单批次图像的文本驱动生成、以图生图微调与遮罩局部重绘；
- **不负责跨镜导演与剪辑**：不裁决全片叙事节奏、不负责多镜头时间轴剪辑；只向外部交付符合尺寸、构图与技术规格的图像候选；
- **双核引擎与模型收敛（默认走 Codex）**：
  - **默认引擎**：技能默认权威指定 `engine="codex"`，生图模型为 **`gpt-image-2.5-sunburst`**（OpenAI Responses API 驱动，具备顶级微观细节解析力、真实皮肤毛孔与面料质感，杜绝传统塑料涂抹感；亦支持极速响应特化的 **`gpt-image-2.5-flare`**）；
  - **备选引擎**：在需要极速概念迭代或多模态指令编辑时，亦可指定 `engine="gemini"`（模型为 `gemini-3.1-flash-image`，Anthropic Messages 协议中继驱动）；
  - 不直接拼接底层第三方 HTTP 请求，不私自跨过 MCP 绕道直连供应商；
- **不可变产物受控归档**：生成的图像由服务端落盘并生成全局唯一不可变 `artifact_id`。客户端下载后必须校验 SHA-256，按项目规范归档至本地 `.output/projects/<project_id>/source/` 目录。

---

## 2. GPT Image 2.5 官方提示词八大核心法则

根据官方最新指南（`GPT-Image-prompting-guide.md`），所有生成与编辑提示词均须严格遵循以下工业化设计法则：

1. **清晰定义交付物 (Define the Result)**：
   - 采用结构化四段式标签模版书写：
     - `[Scene]`：交代空间尺度、环境氛围、光照方向、时间与主色调；
     - `[Subject]`：明确人物身份、具体年龄、体态比例、视线落点与肢体交互动作；
     - `[Details]`：指明取景画幅（如 `16:9` / `1152x2048`）、相机参数隐喻（如 `35mm film photograph, medium close-up at eye level, 50mm lens`）、浅景深与微观纹理（真实毛孔、细微绒毛、真实布料物理垂坠感）；
     - `[Constraints]`：硬性排除项（无过度磨皮、无塑料皮肤、无假体畸形、无多余文字、无水印、无无关 Logo）。
2. **细化人眼可见的真实细节 (Describe Visible Details)**：
   - 杜绝空洞的情绪词，用具体物理质感与真实摄影语言引导（如 "honest, unposed photograph, natural skin texture, visible pores, worn materials"）。
3. **精准文字双引号锁定 (Specify Exact Text)**：
   - 将画面中必需的文案严格置入英文双引号内（如 `"Yours to Create"`）；
   - 显式声明出现次数与位置排布（如 `"Render the tagline exactly once, clearly and legibly integrated into layout. No extra text"`）。
4. **严密隔离“变更项”与“保留项” (Separate Changes from Constraints)**：
   - 在编辑或换装任务中，严格采用“仅修改 X（Change ONLY X）”句式；
   - 详尽列出不可变清单（`"Preserve her exact face, facial features, skin tone, body shape, and pose in every way. Keep lighting and background unchanged"`）。
5. **为多张参考图明确分配职责角色 (Assign Roles to References)**：
   - 在 `image.edit` 中输入多张参考图时，在提示词中逐一分配职责：例如“参考图 1 为主体面容身份，参考图 2 为服装版型款式，参考图 3 为环境底图”。
6. **透明背景双轨规范 (Transparent Cutout)**：
   - 提示词要求：“centered subject, clean alpha edges, no solid backdrop, checkerboard, or watermark”；
   - 调用时必须显式设置 API 参数 `background="transparent"` 并选用 PNG 格式输出。
7. **单步渐进、受控迭代 (Iterate Deliberately)**：
   - 每次只变更单一变量；在后续多轮编辑中反复重申不变量，防止关键面容特征累积漂移。
8. **模型矩阵场景化选型 (Model Selection)**：
   - **画质优先（生产交付 / 角色四视图 / 商业写真 / 终审分镜）**：首选 `gpt-image-2.5-sunburst`（默认，微观解析力最强）；
   - **速度优先（灵感探索 / 多方案快速比选 / 极速低延迟交互）**：首选 `gpt-image-2.5-flare`（延迟大幅削减，画质媲美上一代基准）。

---

## 3. 核心创作任务索引

| 创作类别                                     | 交付文件名                                                                                       | 核心规格与要求                                                                                                                                   | 详细规范                        |
| :------------------------------------------- | :----------------------------------------------------------------------------------------------- | :----------------------------------------------------------------------------------------------------------------------------------------------- | :------------------------------ |
| **人物设计四视图**<br/>(人物圣经 / Identity) | `01-front-neutral.png`<br/>`02-front-smile.png`<br/>`03-left-15deg.png`<br/>`04-right-15deg.png` | 画幅 `1:1` (`2048x2048`) 或 `16:9`；严格胸部以上（Bust-level）中近景；单色纯净背景；无字无水印；显式写明保留真实皮肤毛孔与微小细纹。             | `references/visual-specs.md` §1 |
| **角色服装三视图**<br/>(单元角色 / Wardrobe) | `05-wardrobe-turnaround.png`                                                                     | 7.5 头身极简无脸人台（Faceless Mannequin），杜绝第二张脸污染；正、侧、背三面展示服装剪裁与面料垂坠感。亦支持 `background="transparent"` 透明底。 | `references/visual-specs.md` §2 |
| **分镜单格画面**<br/>(Storyboard Grid)       | `F01.png` ~ `F08.png`                                                                            | 电影画幅 `16:9`，分辨率 2K（`2048x1152`）或 4K（`3840x2160`）；景别机位动态明确；光影调色严格遵从 Visual Bible 圣经。                            | `references/visual-specs.md` §3 |
| **场景概念图**<br/>(Environment)             | `env-<场景名>.png`                                                                               | `16:9` 2K/4K；纯空镜与空间透视，锁定环境光与时代特征。                                                                                           | `references/visual-specs.md` §4 |

---

## 4. MCP 工具与调用契约

服务通过标准 MCP 协议暴露以下核心工具（详见 `references/contracts.md`）：

```text
artifact.import  -> 导入外部已批准的参考素材并校验 SHA-256
image.generate   -> 文本驱动原子生图 (默认 engine="codex", model="gpt-image-2.5-sunburst", quality="high", background="auto")
image.edit       -> 基于 source_artifact_id 与 reference_artifact_ids 执行骨相锁定换装或多图融合
image.inpaint    -> 基于 source_artifact_id 与 mask_artifact_id 执行精准局部重绘
image.status     -> 异步查询任务状态 (queued / running / completed / failed)
image.result     -> 获取完成产物的 artifact_id, sha256, 尺寸与下载路径
image.list       -> 条件检索任务历史列表
```

### 工具调用示例（文生图 · 画质基准主力）：

```json
{
  "project_id": "creator/demo-project",
  "idempotency_key": "B01/wardrobe-v1",
  "engine": "codex",
  "model": "gpt-image-2.5-sunburst",
  "aspect_ratio": "16:9",
  "resolution": "2k",
  "quality": "high",
  "background": "auto",
  "prompt": "[Scene] Pure minimalist studio setting, soft neutral coastal daylight. [Subject] 7.5 heads ratio faceless neutral mannequin wearing futuristic dark-blue mechanic workwear with matte textures. [Details] Shot like a 35mm photograph, eye-level, 50mm lens, natural fabric behavior and clean stitching. [Constraints] Faceless, no human face, no text, no watermarks, no unrelated logos."
}
```

### 工具调用示例（透明背景抠图/Logo）：

```json
{
  "project_id": "creator/demo-project",
  "idempotency_key": "B01/logo-v1",
  "engine": "codex",
  "model": "gpt-image-2.5-flare",
  "aspect_ratio": "1:1",
  "resolution": "2k",
  "quality": "medium",
  "background": "transparent",
  "prompt": "Create an original logo for a local bakery called 'Field & Flour'. Centered single logo, generous padding, clean alpha edges, fully transparent background, no solid backdrop, no checkerboard, no watermark."
}
```

### 工具调用示例（以图生图 · 多图骨相换装）：

```json
{
  "project_id": "creator/demo-project",
  "idempotency_key": "B01/outfit-v1",
  "engine": "codex",
  "model": "gpt-image-2.5-sunburst",
  "source_artifact_id": "art-identity-portrait",
  "reference_artifact_ids": ["art-jacket-ref", "art-boots-ref"],
  "quality": "high",
  "prompt": "Change ONLY the clothing using the provided reference items (Image 1 is identity, Image 2 is jacket, Image 3 is boots). Preserve her exact face, facial features, bone structure, hairstyle, skin tone, and body pose completely unchanged. Natural fabric draping and matching original photo lighting."
}
```

---

## 5. 客户端命令行驱动（CLI）

技能内置纯 Python 标准库命令行客户端，可用于独立调试、批量批处理或 CI 自动化：

```bash
# 1. 提交生图任务并等待下载完成 (默认 engine=codex, model=gpt-image-2.5-sunburst, 自动校验 SHA-256)
python3 scripts/image_client.py generate \
  --project-id "creator/demo-project" \
  --prompt "[Scene] Cyberpunk neon street. [Subject] Woman in rain. [Details] 35mm photo. [Constraints] No plastic skin." \
  --engine codex \
  --model gpt-image-2.5-sunburst \
  --quality high \
  --aspect-ratio 16:9 \
  --output ./cyberpunk-street.png

# 2. 极速探索模式 (使用 gpt-image-2.5-flare)
python3 scripts/image_client.py generate \
  --project-id "creator/demo-project" \
  --prompt "A fast draft concept of a futuristic rover on Mars" \
  --model gpt-image-2.5-flare \
  --output ./rover-draft.png

# 3. 透明背景生成 (background=transparent)
python3 scripts/image_client.py generate \
  --project-id "creator/demo-project" \
  --prompt "Isolated glass perfume bottle, centered, crisp alpha edges" \
  --background transparent \
  --aspect-ratio 1:1 \
  --output ./perfume-cutout.png

# 4. 查询当前任务排队与指标
python3 scripts/image_client.py stats

# 5. 列出项目历史任务
python3 scripts/image_client.py list --project-id "creator/demo-project"

# 6. 查询特定任务详情
python3 scripts/image_client.py status <task-id>
```

配置优先从技能目录的 `.env` 读取；不存在时再从当前目录或上层目录查找 `.env`（变量示例见同目录 `.env.example`）。需要 `IMAGE_MCP_URL` 与 `IMAGE_MCP_BEARER_TOKEN`。

---

## 6. 状态机与容错规则

1. **异步轮询**：`image.generate`、`image.edit`、`image.inpaint` 均为异步操作，立即返回 `task_id`。客户端必须以 2 秒间隔轮询 `image.status`，直到进入 `completed` 或 `failed`。轮询保护超时时间为 180 秒。
2. **幂等性保障**：每次调用必须提供具有确定业务含义的 `idempotency_key`（例如 `<unit_id>/<attempt_id>`）。同一项目下相同键直接返回既有任务，不产生重复扣费与上游负担。
3. **哈希强校验**：产物通过 `/image/artifacts/{id}/content` 下载到本地后，客户端必须实时计算本地文件 SHA-256 并与服务端的 `sha256` 进行强匹配；不匹配时立即中止报错并清理损坏文件。
4. **凭据安全**：禁止在命令参数、日志输出、Prompt 或对话中泄露 Bearer Token；环境变量统一通过 `.env` 或 Kubernetes Secret 注入。

---

## 7. 完成条件

只有当以下条件全部满足时，才能将产物标记为 `ImageCandidate` 移交上层审核门：

1. 图像通过 Image MCP 技术生成成功，任务状态为 `completed`；
2. 图像已下载落盘至项目受控 `source/` 目录，文件非空；
3. 画幅比例与分辨率精确符合规格（如 16:9 / 2K 或 4K）；
4. 本地文件 SHA-256 与服务端登记完全吻合；
5. 不含任何多余文字、水印、参数印章或塑料涂抹畸变。

## 环境变量加载

本技能遵循 [技能环境变量加载规范](../ENVIRONMENT.md)：进程环境变量优先，其次是本技能目录的 `.env`，最后是项目目录的 `.env`。同名变量由高优先级来源覆盖；同目录 `.env.example` 仅用于说明变量，不参与运行时加载。 实际加载由本技能的 `scripts/load_env.py` 统一完成。
