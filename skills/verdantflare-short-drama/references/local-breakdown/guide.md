# VerdantFlare Local Scene Breakdown

输入须为本机 UTF-8 单场剧本文本。先确认本机 Ollama 已运行且 `gemma3:12b` 已安装；不要自动拉取权重，也不要把剧本发到远端端点。用 [本地拆解客户端](../../scripts/breakdown.py) 生成来源锚定的 JSON 草案：

```text
python3 scripts/breakdown.py --input <scene.txt> --output <breakdown.json>
```

脚本只接受回环地址、核对模型是否已安装，并检验每条引用是否为原文连续片段。`facts` 的值还必须直接出现在证据中；不合格项进入 `rejected`。`inferences` 始终待人审，`questions` 只是待核实问题。读取 [输出契约与交接](handoff.md) 后，将可信的原文事实交给 `verdantflare-short-drama` 规划；不要把模型推断或未校验内容直接写成角色圣经、Shot 或 Generation Unit 事实。

一次只处理一场；长剧本先按真实场景边界拆分，不截断。输出已有同名文件时使用新的版本路径，保留旧结果。需要后续图像或视频制作时转交对应 VF Skill，当前技能不调用 Image、Video 或 Music MCP。
