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
    "model": "gpt-image-2",
    "aspect_ratio": "16:9",
    "resolution": "2k",
    "quality": "auto"
  }
  ```
- **参数说明**：
  - `project_id` (string, 必填)：项目标识。
  - `idempotency_key` (string, 必填)：客户端幂等性业务键（格式推荐 `<unit_id>/<attempt_id>`）。同一项目下相同键不会重复生成，直接复用既有记录。
  - `prompt` (string, 必填)：生图提示词，必须明确构图、光影、主体及排除项。
  - `engine` (string, 可选)：底层 API 引擎驱动。**默认指定为 `"codex"`**，亦可选 `"gemini"`。
  - `model` (string, 可选)：覆盖具体模型名。默认缺省时：
    - `engine="codex"`（默认）时默认 `gpt-image-2`；
    - `engine="gemini"` 时默认 `gemini-3.1-flash-image`。
  - `aspect_ratio` (string, 可选)：画幅宽高比。支持 `"16:9"`（默认）、`"9:16"`、`"1:1"`、`"4:3"`、`"3:4"`。
  - `resolution` (string, 可选)：图像分辨率级别。支持 `"2k"`（默认）与 `"4k"`。
  - `quality` (string, 可选)：生成质量偏好。支持 `"auto"`（默认）、`"standard"`、`"hd"`。
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

以既有受控 Artifact 为底图进行多模态指令编辑或风格/服装微调。

- **入参（JSON Schema）**：
  ```json
  {
    "project_id": "creator/demo-project",
    "idempotency_key": "B01/wardrobe-v2-edit",
    "source_artifact_id": "art-9f82d1c0",
    "prompt": "Change the mechanic outfit color from dark-blue to silver-grey, keep the mannequin pose and background unchanged",
    "engine": "codex"
  }
  ```
- **返回结构**：同 `image.generate`，返回 `task_id`、`status`、`created_at`。

---

### 2.4 `image.inpaint`

基于遮罩（Mask）的局部重绘与擦除修复。

- **入参（JSON Schema）**：
  ```json
  {
    "project_id": "creator/demo-project",
    "idempotency_key": "F02/fix-face-v1",
    "source_artifact_id": "art-9f82d1c0",
    "mask_artifact_id": "art-mask-8b21c4e1",
    "prompt": "Fix facial lighting to match warm sunset reflection, blend edges smoothly",
    "engine": "codex"
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
      "model": "gpt-image-2",
      "aspect_ratio": "16:9",
      "resolution": "2k"
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
        "model": "gpt-image-2",
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
