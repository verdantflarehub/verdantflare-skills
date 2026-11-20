---
name: verdantflare-filmset-restore
description: 从影视剧连续原帧去人物还原片场无人空景（Clean Plate）、构建围合五向空间闭合（拓扑 B1）或开放街区多节点链式共边扩展（拓扑 B2）静帧，以及在锁定原帧背景与姿态下执行“只换面孔”带演员效果静帧：完成连续帧遮挡比对与“真实可见／相邻帧或共边补全／模型推断”三层分区标注，通过 verdantflare-image（默认 GPT Image 2.6）多图参考绑定与已定母版共边锚定出图，并生成对照与分层标注审阅板。用于剧照去人还原无人片场、五向围合闭合、开放街区链式扩展与原帧保真换脸静帧参考；不替代 Blender 白模建模、H3 视频生成与人工审美审定。
---

# 从连续剧照还原片场无人场景（verdantflare-filmset-restore）

## 1. 范围与边界

本技能从影视剧连续镜头原帧出发，通过相邻帧遮挡比对与跨镜头共边咬合，把有人物的影视剧照还原为 100% 无人的片场空景底板（Clean Plate），支持两类空间扩展拓扑——**拓扑 B1（围合空间正交五向闭合：东向西、南向北、西向东、北向南、室外含顶俯视或室内无天花板水平剖面俯视）**与**拓扑 B2（开放街区多节点链式共边扩展：以实拍核心还原区为共边与材质锚点，沿主街动线逐节点链式推进推断扩展区，核心区外全部标推断）**，或在此基础上按“只换面孔、天空／顶棚、植物／管线、材质、服装、姿势、位置一律按原帧”硬规则制作带演员静帧参考。本技能只交付静帧场景还原与审阅参考，不替代 Blender 三维白模建模、不调用 H3 或视频生成模型、不代替用户或总导演的人工审美终审；天龙八部聚贤庄、电锯惊魂密室浴室与三国演义安喜县街区为验证案例，其裁切尺寸、时间戳与题字仅作案例说明，不写为通用默认参数。

图像导入、多图参考编辑与生成后内容分级协同 `verdantflare-image` 执行；人物正面半身身份照与无头服装三视图协同 `verdantflare-character-face` 执行；产物受管入库与引用审计协同 `verdantflare-meta-googledrive` 执行；审阅文档排版与结构检查协同 `verdantflare-meta-markdown` 执行；需写入 Studio 工程固定修订时再协同 `verdantflare-artifact` 与 `verdantflare-project`。

## 2. 按任务读取

| 当前任务或阶段                                                   | 读取资料                                     |
| :--------------------------------------------------------------- | :------------------------------------------- |
| 判定单镜去人、空间扩展（五向闭合 B1／街区链式 B2）或带演员静帧   | [分类与边界](references/01.base-category.md) |
| 连续帧比对、摇镜双锚点拆分、三层降级映射、链式推进与失败恢复     | [执行流程](references/01.base-workflow.md)   |
| 还原硬规则、固定与可变层剥离、提示词双模板、画外地标与沙盘防漂移 | [规则约束](references/01.base-rules.md)      |
| 核对清单、九类典型失败案例排查、量化排序门禁与审阅板交付         | [验收标准](references/01.base-acceptance.md) |

执行前先读分类与规则专册确认当前批次边界与还原硬规则，再按流程专册分批出图并对照验收专册逐项自检。

## 3. 工具与命令

本技能自带确定性审阅板合成脚本 [build_clean_plate_boards.py](scripts/build_clean_plate_boards.py)（含上下黑边自动检测裁切对齐、标签右边界钳制与垂直碰撞避让）及回归自测 [test_build_clean_plate_boards.py](scripts/test_build_clean_plate_boards.py)，用于根据 JSON 配置一键生成“原帧／无人场景左右对照板”与“真实可见（`V` 青框）／相邻帧补全（`R` 绿框）／模型推断（`M` 橙框）三层遮挡标注板”：

```text
python -X utf8 scripts/build_clean_plate_boards.py /path/to/board-spec.json
python -X utf8 -m unittest discover -s scripts -p "test_*.py"
```

完整 JSON 字段定义、连续帧抽帧比对命令及 `verdantflare-image` 多图编辑调用参数见[执行流程](references/01.base-workflow.md)。
