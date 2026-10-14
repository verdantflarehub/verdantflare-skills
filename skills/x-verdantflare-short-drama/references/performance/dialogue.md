# 对白与声线交接

## 1. 对白任务

独立对白、旁白、参考录音或单句返修交给 `verdantflare-speech`。先固定剧本修订、角色、句子、镜头与声音版本；单镜表演卡消费同一份台词和声音记录。歌曲、配乐与 RVC 换声仍交给 Music，视频是否支持音频参考或口型按实际 Video 能力决定。

有已安装的 Speech 技能时读取该入口；独立安装本短剧包时，可按需读取[固定 Speech 技能源码](https://github.com/verdantflarehub/verdantflare-skills/blob/edd08111ffe9722ea42018fcd02ae6b628667257/skills/verdantflare-speech/SKILL.md)。不依赖本地兄弟目录，也不自动安装或索取内部服务凭据。

## 2. 当前执行边界

Studio 已接通 speech.capabilities、speech.voices、speech.create、speech.status、speech.result，并完成 Mengsk 旁白生成、原生 Artifact 登记和 Project 回读。实际提交前重新发现工具与 Schema，固定项目修订和 voice_profile；未开放时继续准备并报告当前入口缺口。独立配音使用 Speech，歌曲与视频音轨沿用各自任务边界。

用户明确要生成视频自带对白时沿用该任务范围，并按 Video 的实际能力执行；独立配音文件和可恢复语音任务仍分别验收。

## 3. 短剧回写与听审

默认归档到同一 Studio 短剧项目，用户指定仅本地时遵循其范围。把原生音频、剪辑副本、请求、报告和声音配置与真实角色、声音对象、场景／镜头、状态及制作版本关联；按当前 Project 领域契约维护固定 file_refs。

仅将文件加入项目时报告“已归档，声音素材关联待完成”。服务已经完成提交就回读原修订，不重复导入。换台词或参考形成新 take，未知任务先恢复，单句失败只返修该句。

满幅、错漏字、声线一致性、情绪、自然度和声画同步分别记录。技术成功不替代听审，归档候选不自动选用；不裁切尾字或填满静音来伪造时长符合。
