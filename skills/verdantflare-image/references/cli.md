# Image CLI

命令从技能根目录运行；仅使用 CLI 时读取。本地参数见 `python3 scripts/image_client.py --help`。

技能内置纯 Python 标准库命令行客户端，可用于独立调试、批量批处理或 CI 自动化：

```bash
# 1. 提交生图任务并等待下载完成 (默认 engine=codex, model=gpt-image-2.5-sunburst, 自动校验 SHA-256)
python3 scripts/image_client.py generate \
  --project-id "creator/demo-project" \
  --prompt "[Scene] Cyberpunk neon street. [Subject] Woman in rain. [Details] 35mm photo. [Constraints] No plastic skin." \
  --engine codex \
  --model gpt-image-2.5-sunburst \
  --quality high \
  --aspect-ratio 16:9 \
  --output ./cyberpunk-street.png

# 2. 极速探索模式 (使用 gpt-image-2.5-flare)
python3 scripts/image_client.py generate \
  --project-id "creator/demo-project" \
  --prompt "A fast draft concept of a futuristic rover on Mars" \
  --model gpt-image-2.5-flare \
  --output ./rover-draft.png

# 3. 透明背景生成 (background=transparent)
python3 scripts/image_client.py generate \
  --project-id "creator/demo-project" \
  --prompt "Isolated glass perfume bottle, centered, crisp alpha edges" \
  --background transparent \
  --aspect-ratio 1:1 \
  --output ./perfume-cutout.png

# 4. 查询当前任务排队与指标
python3 scripts/image_client.py stats

# 5. 列出项目历史任务
python3 scripts/image_client.py list --project-id "creator/demo-project"

# 6. 查询特定任务详情
python3 scripts/image_client.py status <task-id>
```


配置行为见 [环境说明](../../ENVIRONMENT.md)。
