---
name: verdantflare-image-codex
description: 使用领域级 image MCP 可靠执行原子图像生成与编辑；当用户或上层 Skill (如 verdantflare-music-mv) 要求生成人物设计四视图、服装人台三视图、单格分镜画面 (F01~F08)、纯场景设计图，或执行局部遮罩重绘 (Inpainting) 时使用。
---

# VerdantFlare Image Codex

专注于将上层编排的视觉意图可靠执行为可审核的 `ImageCandidate`。

## 1. 职责边界

- **原子资产生产**：只负责单张或单批次图像的文本驱动生成、以图生图与局部重绘。
- **不负责跨镜导演与剪辑**：不理解整首歌曲的叙事起承转合、不裁决镜头最终艺术通过；只交付符合尺寸、构图与技术规格的图像候选。
- **协议与通道收敛**：通过宿主或集群注入的 `image.*` MCP 工具执行。默认生图模型权威指定为 `gpt-image-2`，支持选择 `engine="codex"` 或 `engine="gemini"`。不直接拼接底层 HTTP 请求，不管理 GPU 或 Kubernetes 节点。
- **不可变产物受控归档**：生成的图像统一作为不可变 Artifact 登记，并按上层项目结构落盘至对应的 `source/` 原子素材目录，供后续组装脚本（如 `build/compose_inputs.go`）编译为模型多格输入。

---

## 2. 核心创作任务规范

### 2.1 角色设计四视图（人物圣经）
用于冻结人物唯一身份原型（存入 `visualbible.md` 和 `source/`）：
- **规格**：画幅 `1:1` 或 `16:9`，包含胸部以上四种姿态：
  1. `01-front-neutral.png`：正脸中性表情，平视镜头；
  2. `02-front-smile.png`：正脸微笑，自然亲和；
  3. `03-turn-left-15.png`：微转头左侧 15 度；
  4. `04-turn-right-15.png`：微转头右侧 15 度。
- **要求**：纯净浅灰/深蓝单色背景，光影柔和，严禁文字、标签或水印。

### 2.2 角色服装无脸人台三视图
用于单元角色服装审核（体现“角色 = 人物 + 服装”）：
- **规格**：`05-wardrobe-turnaround.png`。
- **要求**：7.5 头身极简木质或灰白无脸人台，展示正、侧、背三面服装剪裁、材质细节与色彩分布。

### 2.3 分镜单格图（Frame F01~F08）与起落幅
用于组成 Picture 2 多格分镜网格（Storyboard Grid）：
- **规格**：`16:9` 电影画幅，分辨率 2K（`2048x1152`）。
- **要求**：主体动态明确、景别（特写/中景/全景）与摄影机运镜意图清晰，色调与 Visual Bible 保持一致。

---

## 3. MCP 工具调用规范

```text
artifact.import  -> 导入已批准的真人正面照或概念参考图 (强制校验 SHA-256)
image.generate   -> 纯文本生成四视图、人台图、分镜格 (默认模型 gpt-image-2)
image.edit       -> 基于底图执行服装微调或风格迁移
image.inpaint    -> 基于纯黑白 Mask 执行局部修复
image.status     -> 异步查询生成进度 (queued / running / completed / failed)
image.result     -> 获取不可变 artifact_id、SHA-256、尺寸与下载路径
```

### 参数建议模板：
* **文生图**：
  ```json
  {
    "project_id": "mengsk/加油吧小月",
    "idempotency_key": "B01/wardrobe-v1",
    "engine": "codex",
    "model": "gpt-image-2",
    "aspect_ratio": "16:9",
    "resolution": "2k",
    "prompt": "Cinematic concept artwork, 7.5 heads ratio mannequin wearing futuristic dark-blue mechanic outfit, clean background, no text"
  }
  ```

---

## 4. 完成条件

只有当图像技术生成成功、文件非空且尺寸比例精确符合预期、SHA-256 已登记到项目受控清单、且本地文件已保存到目标 `source/` 目录后，才能将该图片标记为 `ImageCandidate` 交付给上层审核门。

