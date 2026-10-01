# H3 输入模式与 Provider 边界

## 模型家族能力

MiniMax 官方 Prompt Skill 定义五种模式：T2VA 文本生成；I2VA 首帧向后发展；FL2VA 首尾帧之间生成连续路径；L2VA 向尾帧收敛；Ref2VA 使用图像、视频和音频做全参考。

首尾帧只是 FL2VA，不代表 H3 只有首尾帧。参考图片也不自动等于首帧：Ref2VA 图片默认承担身份、服装、场景、物体或风格职责，必须在 Prompt 中编号并明确保留方式。

## fal MiniMax H3 公开契约

fal 的主入口分为：

- `text-to-video`：Prompt、5–15 秒、480P/768P、六种画幅、seed 和 Prompt Expansion；
- `image-to-video`：可选 `image_url` 首帧和 `end_image_url` 尾帧，画幅跟随首帧；
- `reference-to-video`：多图、多视频和多音频可分别或组合使用，总计最多 12 个文件。视频与音频各自每段 2–15 秒、同类总时长最多 15 秒；至少提供一种参考，音频可以作为唯一参考。

fal 还公开独立 T2V/I2V LoRA 入口，最多 3 个 LoRA，scale 0–4。这些是 fal Provider 能力，不是 MiniMax 基础请求或当前领域 MCP 的通用字段。

fal 的 `prompt_expansion_mode` 会产生可能不同于用户原文的 `expanded_prompt`。当前 `route=fal` 固定关闭 Prompt Expansion；若将来开放，必须同时保存原始 Prompt 和实际提交 Prompt，不允许只保存扩写前文本。

## 当前 VerdantFlare 契约

当前 `video.generate` 只批准业务模型 `minimax-h3-ref2va`。宿主默认渠道为 `h3-vdn`；`fal` 已开放为显式 Ref2VA 渠道，但必须由宿主 MCP 声明可用。本 Skill 不因 fal Provider 支持其他模式而改道：

- 不把 Ref2VA 图片冒充首帧或尾帧；
- `route=fal` 只调用服务端锁定的 `minimax/h3/reference-to-video`，不调用 fal 的 T2V、I2V、FL2V、Prompt Expansion 或 LoRA；
- `route=fal` 的输出分辨率由 Video MCP 固定为 `480P`，Skill 不传分辨率参数；
- fal 的 5–15 秒、最多 12 个文件及多模态限制只适用于 `route=fal`，不能覆盖其他渠道的已批准契约；
- fal endpoint、API Key、供应商 request ID 和结果 URL 都由 Video MCP 管理，不进入 Skill 请求或项目记录；
- 新模式必须先有声明式部署、领域 MCP 字段、输入校验、Provenance 和真实验收，再修改 Skill。

## 模式选择原则

当上层只需要固定开场构图时，需求语义属于 I2VA；需要严格起止构图时属于 FL2VA；只需要身份、造型、动作或声线参考时属于 Ref2VA。当前接口不能满足 I2VA/FL2VA 语义时，应在提交前报告能力缺口，不用 Ref2VA 假装等价完成。
