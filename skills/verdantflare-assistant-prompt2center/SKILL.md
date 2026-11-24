---
name: verdantflare-assistant-prompt2center
description: 抓取公开链接提示词落盘 Google Drive，经 VF Station（默认 GPT Image 2.6，可选 Nano Banana 2.1）出图，预览确认后按三类分类（美女/帅哥/动物）与两级强校验分步沉淀至 VF Center，支持单图无缝扩充为多图。用于个人助理灵感沉淀与多图追加任务；不用于非公开受限内容抓取或绕过人工预览直接入库。
---

# 提示词沉淀 → 出图 → VF Center 灵感资产（v1.1.1）

## 1. 范围与边界

本技能面向 VF 个人助理执行“公开提示词抓取清洗 → VF Station 受控出图 → D6 预览确认 → VF Center 两级强校验分步沉淀”闭环，覆盖新建作品组（单图或首批多图）与对既有作品组（`resource_id`）无缝追加第 2..N 张图或 Edit 迭代图。

抓取清洗复用 `verdantflare-meta-download`，受控出图复用 `verdantflare-image`，最终经 `vf_center.publish_resource` 对应内部接口入库；仅处理公开链接或用户提供的提示词，不绕过人工 D6 预览确认直接写库。本技能为仓库内个人助理技能，安装方式见 `references/install.md`。

## 2. 能力清单与按任务读取

按要执行的原子能力直达对应入口；安装说明见主题专册：

| 原子能力（能干什么活） | 入口文档                                                                      |
| :--------------------- | :---------------------------------------------------------------------------- |
| 抓词入库               | [01.ingest-publish.entrypoint.md](references/01.ingest-publish.entrypoint.md) |
| 一键安装               | [install.md](references/install.md)                                           |

## 3. 工具与命令

本技能元数据位于 [meta.json](meta.json)，依赖通用技能 `verdantflare-meta-download`、`verdantflare-image` 与接口工具 `vf_center.publish_resource`。核心分步上传端点如下（两级必填参数与幂等校验见能力入口）：

```text
POST /internal/assistant/resources
POST /internal/assistant/resources/{resource_id}/items
```
