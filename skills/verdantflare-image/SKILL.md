---
name: verdantflare-image
description: 通过 VerdantFlare Image MCP 服务执行领域级原子图像资产生成、以图生图与局部重绘；当用户或上层编排 (如 verdantflare-music-mv) 需要制作人物设计四视图 (01-front-neutral/02-front-smile/03-left-15deg/04-right-15deg)、服装无脸人台三视图 (05-wardrobe-turnaround)、分镜单格画面 (F01~F08)、纯场景概念图，或执行遮罩局部修复时使用。默认权威采用 codex 引擎 (gpt-image-2)，亦支持 gemini 引擎 (gemini-3.1-flash-image)，包含异步任务状态机管理与产物不可变 SHA-256 校验归档。
---

# VerdantFlare 图像制作 (VerdantFlare Image)

专注于通过 VerdantFlare 领域级 Image MCP 服务，将上层编排的视觉意图可靠生产为技术合格、具有不可变哈希标识的 `ImageCandidate` 资产。

详细契约见 `references/contracts.md`，视觉规格与构图约束见 `references/visual-specs.md`，完整异步流转见 `references/workflow.md`。

---

## 1. 职责边界

- **原子资产生产**：专注于单张或单批次图像的文本驱动生成、以图生图微调与遮罩局部重绘；
- **不负责跨镜导演与剪辑**：不裁决全片叙事节奏、不负责多镜头时间轴剪辑；只向外部交付符合尺寸、构图与技术规格的图像候选；
- **双核引擎与模型收敛（默认走 Codex）**：
  - **默认引擎**：技能默认权威指定 `engine="codex"`，生图模型为 **`gpt-image-2`**（OpenAI Responses API 驱动，适合追求高写实度、面部微表情稳定性与工业级商业质感）；
  - **备选引擎**：在需要快速概念迭代或多模态指令编辑时，亦可指定 `engine="gemini"`（模型为 `gemini-3.1-flash-image`，Anthropic Messages 协议中继驱动）；
  - 不直接拼接底层第三方 HTTP 请求，不私自跨过 MCP 绕道直连供应商；
- **不可变产物受控归档**：生成的图像由服务端落盘并生成全局唯一不可变 `artifact_id`。客户端下载后必须校验 SHA-256，按项目规范归档至本地 `.output/projects/<project_id>/source/` 目录。

---

## 2. 核心创作任务索引

| 创作类别                                     | 交付文件名                                                                                       | 核心规格与要求                                                                                                          | 详细规范                        |
| :------------------------------------------- | :----------------------------------------------------------------------------------------------- | :---------------------------------------------------------------------------------------------------------------------- | :------------------------------ |
| **人物设计四视图**<br/>(人物圣经 / Identity) | `01-front-neutral.png`<br/>`02-front-smile.png`<br/>`03-left-15deg.png`<br/>`04-right-15deg.png` | 画幅 `1:1` 或 `16:9`；严格胸部以上（Bust-level）中近景；单色浅灰/深蓝纯净背景；无字无边框无水印；保留真实毛孔皮肤质感。 | `references/visual-specs.md` §1 |
| **角色服装三视图**<br/>(单元角色 / Wardrobe) | `05-wardrobe-turnaround.png`                                                                     | 7.5 头身极简人台；强制无脸人台化（Faceless），杜绝第二张人脸污染；展示正、侧、背三面服装剪裁与配饰。                    | `references/visual-specs.md` §2 |
| **分镜单格画面**<br/>(Storyboard Grid)       | `F01.png` ~ `F08.png`                                                                            | 电影画幅 `16:9`，分辨率 2K（`2048x1152`）；景别与机位动态明确；光影调色严格遵从 Visual Bible 圣经。                     | `references/visual-specs.md` §3 |
| **场景概念图**<br/>(Environment)             | `env-<场景名>.png`                                                                               | `16:9` 2K；纯空镜与空间透视，锁定环境光与时代特征。                                                                     | `references/visual-specs.md` §4 |

---

## 3. MCP 工具与调用契约

服务通过标准 MCP 协议暴露以下核心工具（详见 `references/contracts.md`）：

```text
artifact.import  -> 导入外部已批准的参考素材并校验 SHA-256
image.generate   -> 文本驱动原子生图 (默认 engine="codex", model="gpt-image-2")
image.edit       -> 基于底图 source_artifact_id 执行服装微调或风格迁移
image.inpaint    -> 基于 source_artifact_id 与 mask_artifact_id 执行局部修复
image.status     -> 异步查询任务状态 (queued / running / completed / failed)
image.result     -> 获取完成产物的 artifact_id, sha256, 尺寸与下载路径
image.list       -> 条件检索任务历史列表
```

### 工具调用示例（文生图）：

```json
{
  "project_id": "creator/demo-project",
  "idempotency_key": "B01/wardrobe-v1",
  "engine": "codex",
  "model": "gpt-image-2",
  "aspect_ratio": "16:9",
  "resolution": "2k",
  "quality": "auto",
  "prompt": "Cinematic concept artwork, 7.5 heads ratio mannequin wearing futuristic dark-blue mechanic outfit, clean neutral background, no text"
}
```

---

## 4. 客户端命令行驱动（CLI）

技能内置纯 Python 标准库命令行客户端，可用于独立调试、批量批处理或 CI 自动化：

```bash
# 1. 提交生图任务并等待下载完成 (默认 engine=codex, 自动校验 SHA-256)
python3 scripts/image_client.py generate \
  --project-id "creator/demo-project" \
  --prompt "未来赛博朋克城市的雨夜街道，霓虹倒影，电影画幅" \
  --engine codex \
  --aspect-ratio 16:9 \
  --output ./cyberpunk-street.png

# 2. 查询当前任务排队与指标
python3 scripts/image_client.py stats

# 3. 列出项目历史任务
python3 scripts/image_client.py list --project-id "creator/demo-project"

# 4. 查询特定任务详情
python3 scripts/image_client.py status <task-id>
```

配置从当前目录或上层目录的 `.env` 自动读取（`IMAGE_MCP_URL` 与 `IMAGE_MCP_BEARER_TOKEN`）。

---

## 5. 状态机与容错规则

1. **异步轮询**：`image.generate`、`image.edit`、`image.inpaint` 均为异步操作，立即返回 `task_id`。客户端必须以 2 秒间隔轮询 `image.status`，直到进入 `completed` 或 `failed`。轮询保护超时时间为 180 秒。
2. **幂等性保障**：每次调用必须提供具有确定业务含义的 `idempotency_key`（例如 `<unit_id>/<attempt_id>`）。同一项目下相同键直接返回既有任务，不产生重复扣费与上游负担。
3. **哈希强校验**：产物通过 `/image/artifacts/{id}/content` 下载到本地后，客户端必须实时计算本地文件 SHA-256 并与服务端的 `sha256` 进行强匹配；不匹配时立即中止报错并清理损坏文件。
4. **凭据安全**：禁止在命令参数、日志输出、Prompt 或对话中泄露 Bearer Token；环境变量统一通过 `.env` 或 Kubernetes Secret 注入。

---

## 6. 完成条件

只有当以下条件全部满足时，才能将产物标记为 `ImageCandidate` 移交上层审核门：

1. 图像通过 Image MCP 技术生成成功，任务状态为 `completed`；
2. 图像已下载落盘至项目受控 `source/` 目录，文件非空；
3. 画幅比例与分辨率精确符合规格（如 16:9 / 2K）；
4. 本地文件 SHA-256 与服务端登记完全吻合；
5. 不含任何文字、水印、参数印章或畸变污染。
