# Music MV 生成后内容分级评估步骤（K09）

## 1. 当前依据

- [18+ 评估规范（rubric v1.0）](content-rating-rubric.md)：判定类别（S1–S4、V1）、四类媒体判定标准及输出 JSON 格式。
- [MV 工作流](workflow.md)：角色图、分镜格、视频单元渲染与 Picture Lock 归档流程。

## 2. 步骤位置

在 MV 制作过程中，凡经 Studio MCP 生成新媒体产物（角色四视图、服装人台、分镜格、视频 Shot 候选或新生成的歌曲候选），均须在“取得生成产物”之后、“单元/项目审核记录展示与 Project 同步”之前执行分级评估：

1. 生成任务完成并取得产物（此时尚未评估）。
2. 按第 3 节准备评估输入，对照 [18+ 评估规范（rubric v1.0）](content-rating-rubric.md) 判定，得到评估 JSON。
3. 调用对应生产域的 `*.rate_content`（`image.rate_content`、`video.rate_content`、`music.rate_content`）提交结果，`general` 也必须提交。
4. 按第 4 节处理 `review.md` 摘要、Project 同步与 World 登记。

只读查询、复用已评估的批准 Master WAV 或恢复已评估任务时，不重复评估。

## 3. 按媒体的评估输入与本机抽帧

- **图像（角色视图、服装人台、分镜格）**：读取生成图像本身，同时评估正向提示词与标题。
- **视频（生成单元 Shot 候选）**：在本机临时目录对下载的视频抽帧，**至少每 2 秒一帧、全片至少 8 帧，另补镜头切换处的关键帧**，结合连续帧序列、音轨、正向提示词与标题判定；**评估后立即删除临时抽帧文件，不上传、不入 Git**，并在 `reasons` 中记录抽帧数量（如无音轨转写能力，另注明 `"未转写音频"`）。
- **音乐与歌词**：评估歌词、正向提示词、标题与转写文本；缺少转写能力时只依据文本资料判定，并在 `reasons` 中注明 `"未转写音频"`。
- **规范版本与不确定从严**：`rubric_version` 固定取自同目录 [content-rating-rubric.md](content-rating-rubric.md) 文首版本（`"rubric v1.0"`）。无法取得完整输入或无法排除 18+ 时，按“不确定即标 nsfw”处理。提交 `*.rate_content` 超时用相同内容幂等重试，不重新生成媒体。

## 4. 交付摘要与项目同步约束

- **摘要与 `review.md` 脱敏**：判定为 `nsfw` 的产物，在交付摘要与单元/项目 `review.md` 中只写“已隐藏产物 N 个（已标记为 NSFW）”，**不展示预览图、接触表、链接、`ContentRef` 或提示词全文**。
- **不同步到 Studio 项目与 World（H1 已确认）**：`nsfw` 产物不同步到 Studio 项目（**不写入 `project.commit`，不进入 Picture Lock 拼接，也不调用 `world.register`**），由用户决定是否改写提示词后重新生成，或由用户在 Studio 设置页开启本机显示后人工取消误标。
