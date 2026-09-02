# H3 输入模式与 Provider 边界

## 模型家族能力

MiniMax 官方 Prompt Skill 定义五种模式：T2VA 文本生成；I2VA 首帧向后发展；FL2VA 首尾帧之间生成连续路径；L2VA 向尾帧收敛；Ref2VA 使用图像、视频和音频做全参考。

首尾帧只是 FL2VA，不代表 H3 只有首尾帧。参考图片也不自动等于首帧：Ref2VA 图片默认承担身份、服装、场景、物体或风格职责，必须在 Prompt 中编号并明确保留方式。

## fal H3 Max 公开契约

fal 的主入口分为：

- `text-to-video`：Prompt、5–15 秒、480P/768P、六种画幅、seed 和 Prompt Expansion；
- `image-to-video`：可选 `image_url` 首帧和 `end_image_url` 尾帧，画幅跟随首帧；
- `reference-to-video`：多图、多视频和多音频，总计最多 12 个文件。视频与音频各自每段 2–15 秒、同类总时长最多 15 秒；音频不能是唯一参考。

fal 还公开独立 T2V/I2V LoRA 入口，最多 3 个 LoRA，scale 0–4。这些是 fal Provider 能力，不是 MiniMax 基础请求或当前领域 MCP 的通用字段。

fal 的 `prompt_expansion_mode` 会产生可能不同于用户原文的 `expanded_prompt`。若将来接入，必须同时保存原始 Prompt 和实际提交 Prompt，不允许只保存扩写前文本。

## 当前 VerdantFlare 契约

当前成都部署与 `video.generate` 只批准 `minimax-h3-ref2va`。本 Skill 不因模型家族或 fal Provider 支持其他模式而改道：

- 不把 Ref2VA 图片冒充首帧或尾帧；
- 不调用 fal 的 T2V、I2V、FL2V、Prompt Expansion 或 LoRA；
- 不把 fal 的 5–15 秒、12 文件限制覆盖本地已批准契约；
- 新模式必须先有声明式部署、领域 MCP 字段、输入校验、Provenance 和真实验收，再修改 Skill。

## 模式选择原则

当上层只需要固定开场构图时，需求语义属于 I2VA；需要严格起止构图时属于 FL2VA；只需要身份、造型、动作或声线参考时属于 Ref2VA。当前接口不能满足 I2VA/FL2VA 语义时，应在提交前报告能力缺口，不用 Ref2VA 假装等价完成。

