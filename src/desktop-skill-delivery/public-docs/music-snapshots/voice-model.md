# 可复用声音模型

> 归属：[World / Music](README.md)；业务发布与授权见 [World 发布设计](../../studio/details/05-studio-world-design.asset-publication.md)。
> 状态：首批领域设计与 `contracts/v2/voice-model.schema.json` 已形成。固定引用、来源及权限已有通用服务验证；Music 适配和真实模型兼容验收尚未完成。

## 资产边界

`voice-model` 表示一套可以被指定推理能力加载的固定模型包。干声/训练素材、模型、歌曲是不同对象：干声不因用于训练就变成模型文件，歌曲使用已有模型不产生新的训练来源。歌曲候选、分轨和母带默认留在歌曲 Project；需要单独复用时另行选取入库。

模型登记保留实际训练或导入项目的修订，文件保持原 ContentRef。直接产于该项目用 produced_in；从外部或其他项目整理的模型用 curated_in，并继续保留 Artifact 原来源。原生训练 run_id、模型 ID 是上游标识，不改写成 World asset_id，也不因换电脑重新训练。

## 固定文件集合

| 文件用途 | 入库要求 | 读取边界 |
| --- | --- | --- |
| 模型权重 | 必须；记录实际存在的文件 | 资产授权范围内读取 |
| 检索索引、配置、词表或其他配套文件 | 由原生加载能力决定是否必需；缺必需文件不得显示可推理 | 与该模型版本一起固定 |
| 领域描述 JSON | 明确文件用途、实际模型格式及经过验证的能力 | 属于选定 files，不另建工程库存 |
| 使用说明 MD | 记录适用声音范围、限制和操作说明 | 按是否需要给使用者阅读明确选入 |
| 验证音频及审核说明 | 保存实际验证依据；可选择可分享的验证片段 | 私有审核不因登记模型自动公开 |
| 原始录音、训练集合、授权证据 | 保留在真实来源项目；不作为推理下载的默认内容 | 需来源权限；如确需复用训练集，单独登记素材资产 |

World 只按明确 file_ids 发布。review_file_id 记录来源审核文件，不授予读取权；需要使用者查看的说明必须明确加入选定 files。模型依赖其他已入库资产时，从来源 Project.asset_refs 明确选择 depends_on；不得将整个训练项目自动变为依赖包。

## 领域 JSON 归属

首版领域文件为 `voice-model.json`，登记为 Project 领域文档，并在入库时与模型文件一起选取。下面字段已收敛至中央 `VoiceModel` Schema；当前通用 World 服务不会替领域适配器验证模型兼容性：

| 字段 | 含义与约束 |
| --- | --- |
| schema_version、kind | 领域格式版本与 voice-model 标识；不替代通用工程版本 |
| native_model | 原生 service_id、model_id；训练 run_id 有真实记录时填写，不伪造 |
| files | weights、index、config 等用途指向通用清单内的逻辑路径；仅表达用途，不复制 Artifact 地址或摘要 |
| compatibility | 原生能力 ID、模型格式/架构、支持版本及实际加载要求；参数名称和取值从原服务接口读取 |
| validation | 固定验证音频路径、实际验证能力/版本、结果与审核说明路径；技术通过与人工选用分别记录 |

逻辑路径以同一固定资产版本的 files 为解析范围；不解析本机绝对路径，不查询模型 head。通用文件清单仍由 Studio 生成，领域适配器只产出业务字段。领域引用必须存在于选定集合，缺文件不以空对象填充。

现有 Music `model.json` 的模型 ID、训练素材、版本、审核和验证记录作为兼容输入；`recordings.json` 继续记录素材授权与准备记录。旧 `assets.json` 的通用库存字段迁至工程清单，领域时长/格式等保留；不能同时维护两套可编辑映射。`VoiceModel` Schema 和 `voice-model.json` 规范已经形成；PW-4/5 仍需完成运行时迁移接入及真实旧工程转换，不因此文出现就宣称旧工程已转换。

## 版本与使用条件

权重、加载必需索引/配置或使用说明变化，发布新的固定资产版本；同一声音主体并不强制共用一个 asset_id，不兼容模型可作为独立资产并记录联系。已有歌曲继续引用原版本；升级通过 project.use_asset 明确提交。

使用前由 Music 适配器解析模型版本及全部必需文件，核对实际推理能力是否支持。缺权重、模型格式不支持、依赖不可读时返回明确缺项，不重新训练补救，不重复原生任务。准备音频的 40 kHz/16-bit/mono 约定属于现行 voice.prepare 输出，不是所有声音模型的推理兼容标准。

## 验收

1. 模型从真实来源项目入库，两个歌曲项目固定引用；更新模型不改变旧歌曲输入。
2. 资产读者可以读取选定模型包，不能因此读取原始录音、未选验证和私有授权资料。
3. 仅整理已有模型不能声明在当前歌曲中重新训练；原 Artifact 来源和原生标识保持不变。
4. 服务重启或换客户端后恢复同一固定版本；无需重新训练或发起收费生成。
5. 领域适配阶段用已批准模型验证加载、配套文件与参数兼容；合成字节的服务测试仅证明来源和引用。

依据：[Music 文件约定](../../../../verdantflare-skills/skills/verdantflare-music/references/artifacts.md)、[Music 流程](../../../../verdantflare-skills/skills/verdantflare-music/references/workflow.md)、[声音准备](../../../../verdantflare-skills/skills/verdantflare-music/references/voice-preparation.md)。这些资料用于设计兼容，本轮不执行媒体生产。
