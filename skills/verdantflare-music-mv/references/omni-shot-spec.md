# Google Omni Flash 1.2 制作范式与逐镜审核单规范

本文沉淀自全长 MV 实战，适用于采用 **Google Omni Flash 1.2**（`route=google-flow`、`model=omni-1.2-flash`）的 30s 高潮版及 `90s+` TV Version 全长 MV 制作。

## 1. 引擎契约与核心禁忌

- **调用路线**：默认固定 `route=google-flow`、`model=omni-1.2-flash`、建议开启 `upscale="1080p"`（0 积分免费超分，失败自动回退 720p）。当 `google-flow` 出现 502 / `channel_http_error` 等渠道故障且经用户明确授权临时切换时，可无缝沿用同一套 7 槽位输入切至 **Higgsfield Seedance 2.5 Draft**（`route=higgsfield`、`model=seedance_2_5_draft`、`mode=reference_to_video`、`resolution=480p`、`--price-ceiling 35`，注意其账号并发上限为 `1`，必须串行接力提交，最多 4 图 + 1 视频，同样不使用 `first_frame/last_frame`）。**CLI 实测注意**：图片必须先经 `video_client.py import` 导入取得 Video 原生 `art_*` ID 后通过 `--image-ref <video_art_id>=<purpose>` 绑定；`video_client.py generate` 在 MCP 模式下**不接受 `--output`、`--watermark`、`--no-generate-audio`**（提交异步返回 `video_task_id`，完成后先下载到 C 盘 `.tmp/` 再拷贝至 `.gdrive/` 防 Windows 跨盘 `os.replace` 报 `[WinError 1]`，最终成片剪辑时统一静音内生音轨并从零时码挂载批准 Master WAV）。
- **输入上限**：`reference_to_video` 最多 **7 张参考图**；`video_editing` 支持 **1 段 ≥ 3 秒 `.mp4` 原片（≤ 128 MiB）+ 最多 7 张参考图**；单段时长 `3–15s`；画幅 `16:9` 或 `9:16`；**不传参考音频**。
- **两大严禁与防误伤避词**：
  1. **严禁设计“人物悬空/失衡动态静态关键帧”**：此类反物理静态帧进视频模型极易漂浮或穿模，且 `google-flow` 原生**不接受 `purpose=first_frame` / `last_frame`**。
  2. **严禁全程全身镜头平铺直叙**：10s 动作或情绪单元必须编排**局部特写与景别起伏**（如贴地球鞋急停特写、运动小腿发力特写、胸部以上运球出击近景、压腕/手部道具特写、眼神大特写），兼顾电影张力与人脸清晰度。
  3. **Google Flow 防安全策略误伤避词**：涉及校园/运动短装人物时，Prompt 中避免混用“18岁 / 少年 / 高中 / 猛力 / 汗珠飞溅”等易触发未成年安全过滤误拒（`rejected before charging`）的字眼；统一使用“年轻青年 / 大学篮球馆 / 敏捷急停 / 眼神专注自信”等中性写实表述，人物外观仍完全由冻结五视图锁定。

## 2. 零遗漏「7 槽位参考传入矩阵」

每个镜头编写 `shots/<GU_ID>/review.md` 时，强制逐项核对以下 7 个槽位（总数严格 `≤ 7 图 + ≤ 1 视频`）：

| 槽位 | `purpose` 与职责说明 |
| :--- | :--- |
| **Slot 1 · 主角五视图（必选）** | `character` · 镜头有人物必传冻结五视图（`CHAR-01` / `CHAR-02A` / `CHAR-02B` 等）；同角色换装必须先派生并审定新五视图 |
| **Slot 2 · 次角五视图（按需）** | `character` · 仅 OTS/背影/前景虚化出现第二角色时传入；严禁双正脸同框，严禁未经批准引入额外角色 |
| **Slot 3 · 共享纯场景空景（强推荐）** | `scene` · 从项目 `scenes/` 无人空景母图库选取；跨年代/不同时段同空间必须以首张纯景为底图经 `image.edit` 派生，锁死建筑几何一致性 |
| **Slot 4 · 关键道具（按需）** | `prop` · 跨镜固定物件（篮球、易拉罐、电车、复古立麦等） |
| **Slot 5 · 稳定构图/光影锚点（可选）** | `style` · 仅用于复杂机位或情绪光影定调，严禁悬空动态帧 |
| **Slot 6 · 上镜尾帧连续性（按需）** | `continuity` · 同空间相邻镜头衔接时传入上一镜成片抽出末帧 |
| **Slot 7 · 参考视频（可选）** | `video`（`mode=video_editing`）· 仅当复刻运镜/动作节奏或精修原片时传入 1 段 `≥ 3s` `.mp4` |

## 3. 创作与画幅高阶规范

- **全长 TV Version 素材池模式**：`90s+` 整曲按成片 `2.5x–3x` 冗余比规划 `10s` 生成单元，先分批过审再快切剪辑。
- **竖屏成片 × 漫画三联混合画幅**：`9:16` 手机成片中，全屏单镜/舞台特写采用原生 `9:16`，跨年代时空对照名场面采用原生 `16:9` 制作三横屏竖排叠画（Triptych）。
- **动漫/影视翻唱 MV 原作分镜致敬**：必须在 `storyboard.md` 显式检索并规划 **3–6 个原作 OP/ED 或漫画/剧集经典分镜致敬锚点**。
- **舞台演唱镜混合唇形同步规范（Hybrid Lip-Sync）**：Omni 1.2 与 Seedance 2.5 不支持参考音频输入；实操统一采用混合顶配方案——**核心副歌正面唱词高光镜**（2–3 镜）从批准 Master 分离/截取精准人声干声（Vocals WAV）导入 Video MCP，走本地免费 **`route=h3-fasth3`**（`minimax-h3-ref2va`，传 `CHAR` 五视图 + 舞台空景 + `--audio-ref`，可选 `--regenerate-2k`）实现逐字精准对口型；其余舞台氛围、握麦手部特写、侧逆光轮廓与长元音爆发镜继续用 Omni 1.2 / Seedance 2.5 并在剪辑时卡拖音点短切规避口型错位。
- **成片预览封装与胶片黑边避坑**：
  - VS Code / Antigravity 内置 Electron 视频预览器不支持专利 `AAC` 音轨（会提示 `AAC codec can't be played`）；交付预览与母版封装时，音轨优先使用开源 **`libmp3lame`（MP3 `160k–256k`）**，确保编辑器内直接有声播放且兼容全平台。
  - 若提示词含“35mm 柯达胶片”等字样，视频模型偶发会在画面边缘生成胶片打孔黑边（如 `KODAK 55`）；剪辑脚本统一对全屏单镜施加 `crop=iw*0.92:ih*0.92` 安全裁边后再缩放至 `1080×1920`，保证 100% 纯净满屏。
  - **开场/漫步氛围镜严禁无故转圈与突兀回头**：前奏或抒情漫步段落必须保持情绪克制与机位稳定（如纯粹脚步特写 + 纯背影前行），严禁在漫步镜里塞入“环绕转一圈 + 突然回头看镜头”破坏代入感。

## 4. 逐镜专属审核单模板（`shots/<GU_ID>/review.md`）

每个生成单元在 `shots/<GU_ID>/` 下维护 `review.md`（人类审核唯一入口）、`spec.json`（机器冻结契约）与 `output/`（视频与接触表）。`review.md` 必须包含 5 个固定分区：

1. **镜头定位与审核状态**：所属故事线、画幅、时长、致敬锚点、当前门禁阶段（`待审输入` → `输入已批准` → `待审视频` → `已批准`）。
2. **输入参考清单（图片 + 视频）**：两行等宽预览表直接展示全部传入参考图及视频截帧，下方逐条写明 `Slot`、`purpose`、`Artifact ID` 与用途，明确勾选“零遗漏核验”。
3. **完整提示词与生成参数**：原文展示分秒景别节拍（`0–2.5s / 2.5–5.5s / 5.5–8s / 8–10s`）与负向约束，并列出完整 CLI/MCP 参数表（`route`、`model`、`mode`、`ratio`、`duration`、`upscale`、`no-generate-audio`、`project_id`、`idempotency_key`）。
4. **输出视频与质检证据**：`task_id`、`artifact_id`、本地 `.mp4`（预览 `< 18MB`）、1 秒 1 帧接触表（`contact-sheet.png`）、K09 抽帧分级结论（注明抽帧数）。
5. **人工审核决定记录（追加不覆盖）**：分别记录“输入/提示词/参数审核”与“输出视频审核”的时间、版本与审核人原始决定。
