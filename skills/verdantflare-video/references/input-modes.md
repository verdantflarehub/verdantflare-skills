# H3 输入模式与 Provider 边界

角色替换输入为同项目 1 段视频与 1 张目标人物图；独立音频配额为 0。源片的 AAC 音轨随输出流复制。

## 模型家族能力

MiniMax 官方 Prompt Skill 定义五种模式：T2VA 文本生成；I2VA 首帧向后发展；FL2VA 首尾帧之间生成连续路径；L2VA 向尾帧收敛；Ref2VA 使用图像、视频和音频做全参考。

首尾帧只是 FL2VA，不代表 H3 只有首尾帧。参考图片也不自动等于首帧：Ref2VA 图片默认承担身份、服装、场景、物体或风格职责，必须在 Prompt 中编号并明确保留方式。

## fal MiniMax H3 公开契约

以下用于理解 Provider 模式，不代表当前 Studio 已开放全部能力；提交前核对宿主所部署适配器的版本和能力声明。

fal 的主入口分为：

- `text-to-video`：Prompt、5–15 秒、480P/768P、六种画幅、seed 和 Prompt Expansion；
- `image-to-video`：可选 `image_url` 首帧和 `end_image_url` 尾帧，画幅跟随首帧；
- `reference-to-video`：多图、多视频和多音频可分别或组合使用，总计最多 12 个文件。视频与音频各自每段 2–15 秒、同类总时长最多 15 秒；至少提供一种参考，音频可以作为唯一参考。

fal 还公开独立 T2V/I2V LoRA 入口，最多 3 个 LoRA，scale 0–4。这些是 fal Provider 能力，不是 MiniMax 基础请求或当前领域 MCP 的通用字段。

fal 的 `prompt_expansion_mode` 会产生可能不同于用户原文的 `expanded_prompt`。现有 Ref2VA 适配器关闭 Prompt Expansion；宿主若开放该能力，应同时保存原始 Prompt 和实际提交 Prompt。

## 当前 VerdantFlare 契约

现有 Studio 网关通过 `video.create` 创建任务，H3 Ref2VA 使用业务模型 `minimax-h3-ref2va`。模型与渠道按真实会话发现，未指定时解析宿主配置并核实可用性；`h3-vdn` 或 Schema 中的 `fal` 不是跨环境默认。以下约束描述现有 fal Ref2VA 适配器，仅在该渠道已配置且就绪时适用：

- 不把 Ref2VA 图片冒充首帧或尾帧；
- `route=fal` 只调用服务端锁定的 `minimax/h3/reference-to-video`，不调用 fal 的 T2V、I2V、FL2V、Prompt Expansion 或 LoRA；
- `route=fal` 的输出分辨率由 Video MCP 固定为 `480P`，Skill 不传分辨率参数；
- fal 的 5–15 秒、最多 12 个文件及多模态限制只适用于 `route=fal`，不能覆盖其他渠道的已批准契约；
- fal endpoint、API Key、供应商 request ID 和结果 URL 都由 Video MCP 管理，不进入 Skill 请求或项目记录；
- 新模式必须先有声明式部署、领域 MCP 字段、输入校验、Provenance 和真实验收，再修改 Skill。

## 本地 H3 首帧条件（`image_guidance="first_frame"`）与两步工作流边界

本地 H3 路由（`route=h3-fasth3` 与 `route=h3-singularity`）均开放 `image_guidance` 参数（取值 `"reference"` 或 `"first_frame"`，默认 `"reference"`）：

- 传 `image_guidance="first_frame"` 时，服务端将 `references.images[0]` 绑定到第 `0` 帧 `PackedLayout` 条件槽位；`images[0]` 分辨率必须与目标输出像素网格精确一致，且不得与 `references.videos` 同时传入。
- 实证控制边界：端到端核查未发现参数丢失证据；单图静止保持对照实验（第 `0` 帧边缘 `IoU 0.9983`）证明首帧条件消费路径真实有效；但在多参考跨画风转换（如拿 3D 白模渲染图作 `images[0]` 首帧，同时叠加多张写实人物/服装参考图并要求去白模写实重绘）下，`first_frame` 属于软条件引导（`hard_geometry_lock=false`），不保证硬几何透视锁定或像素级复制。
- 生产执行边界：凡要求固定机位、精确空间透视且需写实人物定妆的镜头，不得直接将白模或异画风参考塞入多参考视频生成充当硬首帧锁；必须先在图像阶段产出同构图写实首帧定妆图并通过图像层核验，再将该写实首帧送入视频模型执行动作生成。

## 模式选择原则

当上层只需要固定开场构图时，需求语义属于 I2VA；需要严格起止构图时属于 FL2VA；只需要身份、造型、动作或声线参考时属于 Ref2VA。本地 H3 的 `image_guidance="first_frame"` 在同画风写实首帧就绪后可承担首帧引导，但多参考跨画风下不提供硬几何锁定；当前接口不能满足硬锁定或 FL2VA 语义时，应在提交前如实说明边界或先补齐同构图写实首帧定妆图，不用纯多参考 Ref2VA 假装等价完成。
