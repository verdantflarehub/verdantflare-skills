# 从参考录音建立新声线

## 1. 目标与输入

把新的说话参考保存成稳定 `voice_id`，以后只换文稿即可复用。此流程使用 IndexTTS2 参考条件生成，不训练权重。改变年龄、性别或设计不同身份时转[音色设计与候选复用](voice-design.md)，先固定设计输入并发现实际后端，不能把情绪调节或 RVC 模型当成替代。

用户明确要把两种参考的音色结合成新候选时，可先走[实验性声谱融合](voice-morph.md)，再回到本流程登记。该路线有实际跨文本调用证据，但不保证音色稳定或自然度。

沿用当前项目、已有来源说明和授权，缺少名称时可给出可改的标签；参考、原始录音和旧 take 都保留。跨项目复用需通过受控 Artifact/Project 引入参考与配置，不只复制一个不属于目标修订的 ContentRef。

## 2. 准备候选配置

1. 通过 Studio 发现 Schema，保存 `speech.capabilities` 的实际结果，读取当前模型摘要和参考限制。
2. 按[参考接收](voice-reference.md)核验录音。M4A、多声道或过长参考在已授权的 5090 dev 环境派生合格 WAV，记录原件摘要、处理和选段；不静默截断或覆盖。本机仅做静态检查。
3. 用 Artifact 用户导入流程保存有效 WAV，核对上传回执、字节摘要和大小。将服务返回的完整 ContentRef 单独保存为 JSON；不要手写 ID。
4. 在技能目录运行下面的配置工具。路径替换为本项目实际输入，输出目录必须尚不存在。

```sh
python3 scripts/prepare_voice_profile.py \
  --reference reference.wav \
  --content-ref reference.contentref.json \
  --capabilities speech.capabilities.json \
  --voice-id narrator-b \
  --label 旁白B \
  --out voice-candidate-v1
```

`speech.capabilities.json` 保存解包后的工具结果，不是完整 JSON-RPC 信封。`reference.contentref.json` 只含 `store_id/artifact_id/version_id`。工具只用 Python 标准库，检查 PCM/WAVEX 结构与有效音频，输出服务契约内的 `profile.json` 和独立 `intake.json`；退出 0 仅表示配置准备成功。满幅警告保留在 intake，实际转写默认未知，听审和登记状态默认待完成。

工具不执行转码、上传、推理或远端归属查询。内容引用必须与本次实际上传的 WAV 一致；本地摘要匹配不能证明该引用在远端可读。服务端仍会核验归属、模型摘要和内容完整性。

## 3. 登记与回读

把有效参考、`speech/voices/<voice_id>/profile.json` 和必要来源说明作为最小变更提交到原 Project。参考和配置须同时存在于生成使用的固定修订；已有 profile 路径更新时沿用文件身份，保留旧 revision/ContentRef 和旧 take，不覆盖不可变资产。

以返回的真实 `revision_id` 调用 `speech.voices`，核对 `voice_id`、标签、`reference.sha256`、模型摘要、完整 profile ContentRef 和 `compatible=true`。发现错误就修复对应引用或新建模型绑定版本；不随意修改旧配置摘要以消除不兼容提示。完整回读成功才记录为“已登记”；候选仍保持 `candidate/pending`。

Project 提交未知时按原 commit 查询恢复，不能新建一次相同提交。只有原音频上传完成时继续提交即可，不重复上传或重做录音。缺 Project/Artifact 工具按[参考保存依赖](voice-reference.md#4-保存与依赖)处理，保留可继续的配置，不能把本地文件当作已上线声线。

## 4. 新文稿试音与换声

按[任务与交付](tasks-and-delivery.md)用登记的 profile ContentRef 提交 `speech.create`。采用不同于参考内容的简短文稿；比较多种声线时使用相同文稿，例如“下一段旅程即将开始，请和我们一起，看看新的可能。”，若参考恰好已读此句则另选。

先完成当前授权的试音范围；没有必要默认生成多版或重做全片。保存任务、原始 WAV、剪辑副本、报告和来源，分别判断错漏词、音色一致性、自然度与情绪。ASR 或信号检查不代替听审，也不自动设为选用。

确认选用后，后续对白可直接引用这一固定配置。更换成另一声线时只生成受影响句子的新 take；保留旧声线、旧音频和音乐轨。检查真实时长与锁定窗口，不能截尾字、变速或静默改文稿来对齐。
