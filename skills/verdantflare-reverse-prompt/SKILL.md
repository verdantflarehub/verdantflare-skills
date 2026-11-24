---
name: verdantflare-reverse-prompt
description: 从单张场景图（实拍剧照、定妆参考图、穿搭图、空景片场图或已还原 Clean Plate）逆向反推高保真、零漏项、可直接用于文生图模型的结构化分块提示词。支持单人完整场景八段式（默认标杆）、无人纯场景七段式与多人互动剧照分角色八段式，默认中文分块直出并支持参数化模板与英文自然语言变体，附带确定性校验与解析脚本。用于单图反推生图提示词、镜头造型与环境文字化拆解；不用于多图综合反推、视频自动抽帧反推、整片参考资产沉淀（归 verdantflare-reference-learning）、像素级去人空景还原（归 verdantflare-filmset-restore）或默认自动触发付费生图。
---

# 从单图场景反推提示词（verdantflare-reverse-prompt）

## 1. 范围与边界

本技能第一版本（v1）专用于**单张图片到结构化生图提示词**的逆向工程：通过前置视觉盘点，把单张场景图按场景类型确定性拆解为可直接复制到生图模型（如默认 GPT Image 2.6、Nano Banana、ChatGPT）的中文分块提示词，支持单人完整场景（默认八段式）、无人纯场景（七段式）与多人互动剧照（分角色八段式），并可按需切换为 `` `{{变量}}` `` 参数化模板或英文自然语言变体。本技能不处理多角度组图综合或视频抽帧反推（留待后续版本），不替代 `verdantflare-reference-learning`（整片/整曲拆解与 World 沉淀）与 `verdantflare-filmset-restore`（连续帧去人空景还原），默认不自动调用生图或视频服务，也不替代人工审美终审。

需对反推提示词做纯文生图闭环复现验证时，协同 `verdantflare-image`（默认 `engine="codex"`，`model="gpt-image-2.6-sunburst"`）执行并按 K09 完成生成后内容分级；审阅文档排版协同 `verdantflare-meta-markdown`；如需长期留存验证样图协同 `verdantflare-meta-googledrive`。

## 2. 能力清单与按任务读取

按单图场景类型直达对应原子能力入口；跨能力硬规则、验收门禁与分块模板见公共专册：

| 原子能力 | 入口文档                                                                        |
| :------- | :------------------------------------------------------------------------------ |
| 单人反推 | [01.single-portrait.entrypoint.md](references/01.single-portrait.entrypoint.md) |
| 空景反推 | [02.empty-scene.entrypoint.md](references/02.empty-scene.entrypoint.md)         |
| 多人反推 | [03.multi-character.entrypoint.md](references/03.multi-character.entrypoint.md) |
| 分块模板 | [templates.md](references/templates.md)                                         |

先读目标场景能力的 `entrypoint.md` 确认六项视觉盘点与分块步骤，再结合公共规则与 `templates.md` 产出正文并运行校验脚本。

跨能力公共文档：[00.common.rules.md](references/00.common.rules.md)、[00.common.acceptance.md](references/00.common.acceptance.md)（由各能力入口引用）。

## 3. 工具与命令

本技能自带零外部依赖校验与解析脚本 [prompt_lint.py](scripts/prompt_lint.py) 及回归测试 [test_prompt_lint.py](scripts/test_prompt_lint.py)，用于校验分块完整性、画幅声明、负向约束配对、占位符与水印污染拦截，并将合规文本解析为 JSON：

```text
python -X utf8 scripts/prompt_lint.py lint /path/to/prompt.txt --category c1
python -X utf8 scripts/prompt_lint.py parse /path/to/prompt.txt --category c1 --json
python -X utf8 -m unittest discover -s scripts -p "test_*.py"
```

完整参数（`--variant default|template|en`、`--json`）、变体导出方式与闭环复现命令见各能力入口与公共规范。
