---
name: verdantflare-assistant-prompt2center
description: 抓取公开链接提示词落盘 Google Drive，经 VF Station（默认 GPT Image 2.6，可选 Nano Banana 2.1）出图，预览确认后按三类分类（美女/帅哥/动物）与两级强校验分步沉淀至 VF Center，支持单图无缝扩充为多图。
---

# 提示词沉淀 → 出图 → VF Center 灵感资产（v1.1.0）

## 1. 触发场景

- **新建作品（单图或首批多图）**：用户发送公开 X/网页链接或提示词，要求「沉淀提示词、出图并发布到 VF Center」。
- **单图扩充为多图**：用户对既有作品组（`resource_id`）要求「再用 Nano Banana 跑一张」或「用 Edit 换姿势/换穿搭追加到这组」，无需迁移数据，直接向原 `resource_id` 追加第 2..N 张图即可自动变为多图。

## 2. 两级上传参数强契约（防漏参、防大包超时）

### 2.1 作品组级参数（建组传一次）

| 参数 | 规则 |
| --- | --- |
| `media_type` | 必填，`image` 或 `video` |
| `subject_category` | 必填强制枚举：`beauty`（美女）/ `handsome`（帅哥）/ `animal`（动物） |
| `title` | 必填，自动剥离原帖口水话后的精炼标题（≤ 36 字） |
| `prompt` | 必填，纯净基准/母版提示词（已去口水话） |
| `prompt_origin` | `extracted`（原帖提取）或 `adapted`（二次改编） |
| `source_url` / `source_author` | 公开原帖链接与 `@作者`（自研可留空） |

### 2.2 媒体项级参数（每张图独立分步提交）

| 参数 | 规则 |
| --- | --- |
| `item_id` | 必填幂等键（断点重试不重复插入） |
| `label` | 必填展示标签（如 `GPT Image 2.6`、`Nano Banana 2.1`、`换穿搭 · 绯红马面裙`） |
| `step_label` | 可选步骤号（如 `① 母版 v1`、`② 换姿势`） |
| `engine` / `model_name` | 必填真实引擎与模型编码（如 `codex` / `gpt-image-2.6-sunburst`） |
| `aspect_ratio` / `resolution` | 画幅（如 `3:4`）与从图片头部解码出的真实像素（如 `1024 × 1536`） |
| `edit_prompt` | 可选，仅 Edit 迭代图填写；有值时详情页自动在「提示词」上方高亮展示「编辑指令」 |
| `image_mime` / `image_base64` | 单张原始图片字节（严禁把多张 2K 图塞进同一个请求体） |

## 3. 固定执行流程

1. **抓取与清洗（复用 `verdantflare-download`）**：抓公开推文正文，剥离「提示词在这里：」之前的感叹口水话，生成干净 `title` 与纯净 `prompt`，同步写入 `/var/lib/assistant/drive/prompts/`。
2. **受控出图（复用 `verdantflare-image`）**：默认 `engine="codex"`、`model="gpt-image-2.6-sunburst"`；指定 Nano Banana 时使用 `engine="gemini"`、`model="gemini-nano-banana-2.1"`。下载成图后解码真实像素 `WxH` 并完成内容分级门禁。
3. **D6 预览确认（必停点）**：把生成预览图与拟入库的 **分类（美女 / 帅哥 / 动物）**、**新建或追加目标 `resource_id`**、**标题**、**模型与 Edit 词** 明确呈报 Mengsk，确认无误才入库。
4. **分步原子上传与回读核验**：
   - 新建作品：先调 `POST /internal/assistant/resources` 建组并上传首图；
   - 扩充多图：逐张调用 `POST /internal/assistant/resources/{resource_id}/items` 幂等追加；
   - 上传完成后回读作品详情核验 `itemCount` 与每张图 `imageSha256`，确认零缺图后返回 `https://hub.verdantflare.ai/prompts/images/<resource_id>`（或 `/prompts/videos/<resource_id>`）。
