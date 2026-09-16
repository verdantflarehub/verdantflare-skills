# MiniMax H3 Ref2VA Prompt 编译

## 先确定 Generation Unit 形态

不要把 Generation Unit 和剪辑意义上的 Shot 混为一谈。一个 4–15 秒单元只选一种形态：

- **连续单镜**：一个场景、一个主要动作链、一个连续相机行为；用 2–4 个时间 beat 描述状态变化，不写 `cut`。人物移动、复杂道具交互和连续空间动作优先使用此形态。
- **内部多镜**：最多 2–3 个 Shot，用严格递增的切点；每次切镜必须引入新的信息、空间、状态或视点。广告化蒙太奇和短对白可使用。
- **外部拆分**：换场、换装、换时间、换屏幕方向，或动作链超出 10 秒可可靠表达的范围时，由上层拆成多个 Generation Unit。H3 Skill 不在一个 Prompt 中跨单元合并。

若上层没有冻结形态，停止提交并返回这个缺口。不要自行把连续单镜改成蒙太奇，也不要用长 Prompt 掩盖不可控复杂度。

## 使用官方六段式 Ref2VA 结构

最终传给 Ref2VA 的 Prompt 固定使用以下英文段落和顺序；对白、歌词与画面文字保留原语言：

```text
subject_definitions:
summary:
retention_analysis:
detailed_description:
overall_soundscape:
non_diegetic_music:
```

`subject_definitions` 为实际输入建立稳定标签：`<Subject 1>`、`<Picture 1>`、`<Video 1>`、`<Audio 1>`。定义可观察内容和唯一职责，不把同一张图片同时写成身份、动作和场景的万能参考。

`summary` 用短段落声明任务类型、目标视频和引用关系，不堆镜头细节。

`retention_analysis` 逐项写明：`fully_preserved` 表示外观、构图或源视频需完整保留；`partially_copy` 表示只复制指定音频、动作区间或环境元素；`reference` 表示用于身份、声线、服装、动作或摄影参考，但不逐帧复制。

`detailed_description` 按播放顺序写构图、人物、环境、动作、相机、光线、同步声音和引用生效点。内部切镜使用官方格式：

```text
[Shot 1] ...
[Shot 2] At 00:03.500, the camera cuts to ...
```

第一个 Shot 不写起始时间；后续切点严格递增并落在请求时长内。

`overall_soundscape` 只总结环境声、物理动作声和非语言人声。对白与歌唱写在 `detailed_description`，使用稳定说话人 ID 和 `<d>[Language] ...</d>`。`non_diegetic_music` 只描述角色听不到的配乐；无配乐时写 `N/A`。

## 10 秒连续单镜模板

模板中的时间是动作 beat，不是切镜：

```text
subject_definitions:
<Subject 1> is ... in <Picture 1>.
<Picture 1> is the identity reference for <Subject 1>'s face only.
<Picture 2> is the current wardrobe and adult full-body proportion reference.
<Video 1> is the exact ten-second temporal reference for body performance and camera path.

summary:
[identity reference + wardrobe reference + temporal motion reference] The target is one uninterrupted ten-second shot ...

retention_analysis:
<Subject 1>: fully_preserved - ...
<Picture 1>: reference - face identity only.
<Picture 2>: reference - wardrobe and full-body proportions only.
<Video 1>: reference - exact action tempo, body mechanics, and camera trajectory across the full ten seconds.

detailed_description:
The target video is ...
[Shot 1] At 0.00-3.00 seconds, establish the starting composition, body state, space, visible light sources, and action onset. At 3.00-7.00 seconds, <Subject 1> completes the primary action with observable contact, weight transfer, and continuous displacement while the camera performs one named movement at a stated speed and amplitude. Foreground and background landmarks move in the specified directions with visible parallax while subject scale remains stable. At 7.00-10.00 seconds, the action causes a visible result and settles into the approved ending composition without a cut.

overall_soundscape: ...

non_diegetic_music: N/A
```

## 10 秒内部多镜模板

```text
detailed_description:
[Shot 1] ... establish one state and action.
[Shot 2] At 00:03.500, the camera cuts to ... reveal new information and complete one action.
[Shot 3] At 00:07.000, the camera cuts to ... deliver the payoff and hold a clean ending state.
```

不要在十秒内塞入超过三个 Shot。每个 Shot 至少有一个可观察动作，不能只生成三张漂亮静态构图再用推近和溶解连接。

## 写可审核的主体运动

按“起始状态 → 接触与重心 → 动作周期 → 空间位移 → 结束状态”写。人物走跑示例：

```text
<Subject 1> completes at least two continuous jogging stride cycles: left and right feet alternately contact and push off from the pavement, the arms swing opposite the legs, the pelvis and torso advance several meters, and the stride settles into the specified final pose.
```

驾驶和物体交互同样要写因果：手先操作，机械部件响应，主体和环境随后变化。避免只写 `dynamic`、`natural movement`、`cinematic` 或 `emotional climax`。

## 写可审核的相机运动

相机表达包含类型、幅度、速度和主体关系。使用明确区分的术语：

- `Zoom In / Zoom Out`：机位不动，改变焦距；
- `Push In / Pull Out`：摄影机前后位移；
- `Pan / Tilt`：机位不动，镜头水平或垂直旋转；
- `Truck / Pedestal`：摄影机水平或垂直位移；
- `Arc Shot / Tracking Shot / Static Shot / POV / Roll / Shake`。

例如：

```text
The camera tracks beside her at matched speed with small amplitude and a fixed focal length, keeping her at constant scale in the middle third of frame. Door frames and path lamps translate steadily right-to-left at different speeds with visible parallax.
```

不要在一个连续单镜里同时要求正面跟拍、变侧面、超车、环绕、越过人物和回到原位。需要这些变化时改为内部多镜或外部拆分。

## 写光线而不是只写气氛

说明可见光源、方向、软硬、色温和变化。例如“screen-left window provides a soft cool key; warm ceiling practicals create a restrained rim on her hair”。`cinematic lighting` 不能替代灯位。参考图已有明确光线时，优先要求保留，不叠加互相矛盾的新光源。

## 针对照片插值的退化约束

先写完整的正向动作和摄影路径，再按当前风险加入少量排除：

```text
No digital zoom, pose holding, still-photo interpolation, scale jump, recentering snap, montage, dissolve, cut, slow motion, or running in place. Do not simulate forward motion by enlarging the subject; preserve a fixed focal length and show continuous ground translation and background parallax.
```

负面词不是独立控制通道。模型反复走同一退化路径时，缩短单元、减少参考冲突、换成等长动作参考或改用内部切镜；不要无限堆叠同义否定词。

## 参考素材硬规则

- 动作参考视频必须覆盖目标时长；不得把 4 秒动作视频循环或拉长去驱动 10–15 秒输出。
- 头肩身份卡只负责脸，不能定义全身比例、裤裙、鞋或复杂动作。
- 全身移动镜头增加当前造型的全身卡；驾驶、奔跑和复杂表演增加对应动作视频。
- 参考冲突时减少输入或创建新的 Generation Unit Version，不用更多参考掩盖冲突。

## 生成后的停止条件

第一次动态代表镜头未通过完整播放和逐秒接触表时，停止同类批量生成。核对每个动作、切点、相机路径、地面位移、背景视差、主体尺度、身份、光线和声音；不能把编码成功、首尾帧好看或高帧差当作 Prompt 已验证。

