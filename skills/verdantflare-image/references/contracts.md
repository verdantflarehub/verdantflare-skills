# VerdantFlare Image MCP 工具与接口契约

本文档详述 VerdantFlare Image MCP 服务的工具定义、参数结构、数据模型与生命周期状态机。所有智能体与客户端在构造请求或解析响应时均须以此为准。

---

## 1. 认证与端点

- **公网基础端点**：`POST ${IMAGE_MCP_URL}`
- **监控看板**：`GET ${IMAGE_MCP_URL}/dashboard`
- **REST 任务统计**：`GET ${IMAGE_MCP_URL}/api/tasks/stats`
- **产物下载接口**：`GET ${IMAGE_MCP_URL}/artifacts/{artifact_id}/content`
- **HTTP 鉴权**：`Authorization: Bearer <IMAGE_MCP_BEARER_TOKEN>`

---

## 2. 工具列表与契约

### 2.1 `artifact.import`

导入外部已批准的参考素材（如真人正面近照、角色设计图底图），经 SHA-256 完整性校验后转存为受控不可变 Artifact。

- **入参（JSON Schema）**：
  ```json
  {
    "project_id": "creator/demo-project",
    "source_url": "https://assets.example.com/assets/ref-face.png",
    "filename": "01-front-neutral-ref.png",
    "expected_sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
  }
  ```
- **参数说明**：
  - `project_id` (string, 必填)：所属项目标识，用于目录与数据隔离。
  - `source_url` (string, 必填)：待下载外部资源的 HTTPS 链接，必须在允许来源白名单内。
  - `filename` (string, 必填)：目标产物文件名。
  - `expected_sha256` (string, 必填)：64 位十六进制 SHA-256 哈希值。哈希不匹配时导入中止并报错。
- **返回结构**：
  ```json
  {
    "status": "completed",
    "project_id": "creator/demo-project",
    "artifact": {
      "artifact_id": "art-9f82d1c0",
      "project_id": "creator/demo-project",
      "filename": "01-front-neutral-ref.png",
      "sha256": "e3b0c442...",
      "size_bytes": 1048576,
      "media_type": "image/png",
      "created_at": "2026-09-09T12:00:00Z"
    },
    "download_path": "/image/artifacts/art-9f82d1c0/content"
  }
  ```

---

### 2.2 `image.generate`

文本提示词驱动的原子图像生成。创建异步任务，返回任务 ID 供轮询。

- **入参（JSON Schema）**：
  ```json
  {
    "project_id": "creator/demo-project",
    "idempotency_key": "B01/wardrobe-v1",
    "prompt": "Cinematic concept artwork, 7.5 heads ratio mannequin wearing futuristic dark-blue mechanic outfit, clean neutral background, no text",
    "engine": "codex",
    "model": "gpt-image-2.5-sunburst",
    "aspect_ratio": "16:9",
    "resolution": "2k",
    "quality": "high",
    "background": "auto"
  }
  ```
- **参数说明**：
  - `project_id` (string, 必填)：项目标识。
  - `idempotency_key` (string, 必填)：客户端幂等性业务键（格式推荐 `<unit_id>/<attempt_id>`）。同一项目下相同键不会重复生成，直接复用既有记录。
  - `prompt` (string, 必填)：生图提示词，建议采用官方四段式结构（`[Scene]`、`[Subject]`、`[Details]`、`[Constraints]`），精准文字须置于英文双引号内。
  - `engine` (string, 可选)：底层 API 引擎驱动。**默认权威指定为 `"codex"`**，亦可选 `"gemini"`。
  - `model` (string, 可选)：覆盖具体模型名。
    - `engine="codex"`（默认）时推荐：
      - **`gpt-image-2.5-sunburst`**（默认，画质基准主力，微观毛孔、发丝质感与复杂光影全面领先，杜绝塑料涂抹感）；
      - **`gpt-image-2.5-flare`**（极速响应小模型，适合多方案快速探索或延迟敏感流程）；
      - `gpt-image-2`（向下兼容）；
    - `engine="gemini"` 时默认 `gemini-3.1-flash-image`。
  - `aspect_ratio` (string, 可选)：画幅宽高比。支持 `"16:9"`（默认）、`"9:16"`、`"1:1"`、`"4:3"`、`"3:4"`。
  - `resolution` (string, 可选)：图像分辨率级别。支持 `"2k"`（默认）与 `"4k"`（4K 严格遵循官方上限：单边 <= 3840 像素且为 16 整倍数，如 16:9 为 `3840x2160`，9:16 为 `2160x3840`，1:1 为 `2048x2048`）。
  - `quality` (string, 可选)：生成质量偏好。官方支持：`"auto"`、`"low"`、`"medium"`、`"high"`（默认，极致保留微观细节）、`"xhigh"`、`"max"`（历史 `"hd"` 自动平滑兼容映射为 `"high"`）。
  - `background` (string, 可选)：背景模式。支持 `"auto"`（默认）、`"transparent"`（生成纯透明背景，输出含 Alpha 通道之 PNG/WebP）、`"opaque"`（纯色/实体景深背景）。
- **返回结构**：
  ```json
  {
    "task_id": "img-task-a72e81fc",
    "status": "queued",
    "created_at": "2026-09-09T12:00:01Z"
  }
  ```

---

### 2.3 `image.edit`

以既有受控 Artifact 为底图进行多模态指令编辑、骨相锁定换装或多图融合合成。

- **入参（JSON Schema）**：
  ```json
  {
    "project_id": "creator/demo-project",
    "idempotency_key": "B01/wardrobe-v2-edit",
    "source_artifact_id": "art-9f82d1c0",
    "reference_artifact_ids": [
      "art-jacket-01",
      "art-boots-02"
    ],
    "prompt": "Change ONLY the clothing using the provided reference items. Preserve her exact face, facial features, skin tone, body shape, and pose in every way. Do not change the background or lighting.",
    "engine": "codex",
    "model": "gpt-image-2.5-sunburst",
    "quality": "high",
    "background": "auto"
  }
  ```
- **参数说明**：
  - `source_artifact_id` (string, 必填)：主参考图 Artifact ID（通常为主体肖像或主场景底图）。
  - `reference_artifact_ids` (array[string], 可选)：附加参考图 Artifact ID 列表（例如服饰单品图、参考道具图或第二角色图，支持多图融合）。
  - `prompt` (string, 必填)：编辑指令。务必遵循官方“严密隔离变更项与保留项”原则，使用“Change only X”句式并明确指出哪些特征绝对不可变。若输入多张参考图，须在提示词中明确分配各图职责（如“图 1 为面容身份，图 2 为服装款式”）。
  - `engine` (string, 可选)：默认 `"codex"`。
  - `model` (string, 可选)：`"gpt-image-2.5-sunburst"` 或 `"gpt-image-2.5-flare"`。
  - `quality` (string, 可选)：`"auto"`, `"low"`, `"medium"`, `"high"`, `"xhigh"`, `"max"`。
  - `background` (string, 可选)：`"auto"`, `"transparent"`, `"opaque"`。
- **返回结构**：同 `image.generate`，返回 `task_id`、`status`、`created_at`。

---

### 2.4 `image.inpaint`

基于遮罩（Mask）的精准局部重绘与无痕物体抹除。

- **入参（JSON Schema）**：
  ```json
  {
    "project_id": "creator/demo-project",
    "idempotency_key": "F02/fix-face-v1",
    "source_artifact_id": "art-9f82d1c0",
    "mask_artifact_id": "art-mask-8b21c4e1",
    "prompt": "Fix facial lighting to match warm sunset reflection, blend edges smoothly",
    "engine": "codex",
    "model": "gpt-image-2.5-sunburst",
    "background": "auto"
  }
  ```
- **返回结构**：返回 `task_id`、`status`、`created_at`。

---

### 2.5 `image.status`

查询异步生图任务的当前进度与状态。

- **入参**：`task_id` (string, 必填)
- **返回结构**：
  ```json
  {
    "task_id": "img-task-a72e81fc",
    "status": "completed",
    "created_at": "2026-09-09T12:00:01Z",
    "updated_at": "2026-09-09T12:00:15Z",
    "duration_seconds": 14.2,
    "artifact_id": "art-3c4d5e6f",
    "error": null
  }
  ```
- **状态枚举（Status）**：
  - `queued`：排队等待 Worker 协程消费。
  - `running`：正在向模型 API 发起请求与流式出图。
  - `completed`：出图成功并已受控落盘，已生成 Artifact 记录。
  - `failed`：生成失败，`error` 字段包含具体原因。
  - `canceled`：任务被取消。

---

### 2.6 `image.result`

在任务状态为 `completed` 后获取最终产物元数据与下载路径。

- **入参**：`task_id` (string, 必填)
- **返回结构**：
  ```json
  {
    "task_id": "img-task-a72e81fc",
    "artifact_id": "art-3c4d5e6f",
    "project_id": "creator/demo-project",
    "filename": "wardrobe-v1.png",
    "sha256": "4b227777d4dd1fc61c6f884f48641d02b4d121d3fd328cb08b5531fcacdabf8a",
    "size_bytes": 2621440,
    "metadata": {
      "prompt": "...",
      "engine": "codex",
      "model": "gpt-image-2.5-sunburst",
      "aspect_ratio": "16:9",
      "resolution": "2k",
      "quality": "hd"
    },
    "duration_seconds": 14.2,
    "download_path": "/image/artifacts/art-3c4d5e6f/content"
  }
  ```

---

### 2.7 `image.list`

分页与状态条件检索任务列表，供看板审计或历史恢复。

- **入参**：
  - `project_id` (string, 可选)
  - `engine` (string, 可选)
  - `status` (string, 可选)
  - `limit` (int, 默认 50)
  - `offset` (int, 默认 0)
- **返回结构**：
  ```json
  {
    "tasks": [
      {
        "task_id": "img-task-a72e81fc",
        "project_id": "creator/demo-project",
        "status": "completed",
        "engine": "codex",
        "model": "gpt-image-2.5-sunburst",
        "prompt_preview": "Cinematic concept artwork...",
        "duration_seconds": 14.2,
        "artifact_id": "art-3c4d5e6f",
        "created_at": "2026-09-09T12:00:01Z"
      }
    ],
    "total": 1,
    "limit": 50,
    "offset": 0
  }
  ```

---

## 3. 产物落盘与文件下载

任务完成后，不可变图片已在服务端落盘（存储于持久卷 `/data/projects/{project_id}/` 下）。

客户端获取图像内容流程：

1. 拼接绝对 URL：`${IMAGE_MCP_URL}` + `download_path`（或从服务端对应接口下载）；
2. 携带 HTTP Header `Authorization: Bearer <IMAGE_MCP_BEARER_TOKEN>` 发送 GET 请求；
3. 下载保存到本地项目 `source/` 目录；
4. 校验本地文件的 SHA-256 哈希值必须与 `image.result` 返回的 `sha256` 完全一致。
