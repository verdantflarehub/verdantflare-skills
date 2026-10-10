---
name: verdantflare-blender
description: Inspect and edit a specified VerdantFlare Blender instance through its Studio MCP connection, save scenes to Project and recover known operations. Use for live scene work; not for Blender tutorials, offline code review or application deployment.
---

# VerdantFlare Blender

## 1. 选择实例

使用用户明确的 Studio 实例连接，例如 `/mcp/blenderA`，从已有连接或受控目录取得完整地址。
它与中央 `/mcp` 分别发现工具；同一个有效 Studio Bearer 可复用，实例权限仍逐个校验。
不得因 A 不可用转到 B，不使用独立下游公网 MCP。

先核对真实 tools/list、绑定 project_id 和任务范围。现有名字包括 session.open、scene.get、object.create、project.save；不套用早期 blender.* 草案名。
这里的 project.save 属于实例入口，不发送到中央 Project 域。

## 2. 场景操作

按[会话与恢复](references/sessions-and-recovery.md)打开 read/edit 会话，核对实例、项目、代次。
GUI 或其他编辑者占用时报告，不抢占控制权或重启实例。

使用 scene.get 读取基线，只执行用户任务需要的对象操作。变更前固定幂等键、原参数和要求的 scene_version。
操作后读回对象与场景版本；超时先对账，不重复创建对象。
任意 Python、长渲染或动态实例创建只有在目标工具真实支持且任务授权时才可另行规划，不伪造接口。

## 3. 保存与验证

scene.save 只保存实例工作区。用户要求保存到项目时，按[工程归档](references/project-save.md)调用实例 project.save。
等待 operation.get 明确完成，再从中央 Project/Artifact 回读固定修订和 `.blend` 内容。
应用已执行上传和提交，客户端不重复保存同一检查点。

哈希正确只证明字节完整。需要证明可继续编辑时，必须实际加载并核对场景；首次恢复使用服务提供的受控流程，不能删除引用或改空项目绕过。

## 4. 收尾

单次任务释放本次取得的会话，长期交互按用户要求保留。释放失败记录恢复入口，不关闭其他人的会话。
返回实例、操作与保存状态、Project 修订、内容校验及已知缺口；技术成功不代替创作或视觉验收。
