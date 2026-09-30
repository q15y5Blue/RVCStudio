# Applio + RVC v2 实时中文男声转女声完整配置指南

> 适用环境：**Windows + NVIDIA RTX 3070**
>
> 目标：**中文普通话、男声 → 自然女声、实时聊天、尽可能低延迟**
>
> 推荐链路：
>
> **实体麦克风 → Applio Realtime → RVC v2 女声模型 → CABLE Input → CABLE Output → 微信 / Discord**

---

## 1. 安装 Applio

对 Windows + NVIDIA 显卡，建议优先使用 **Applio 官方预编译 Windows 版**，第一遍不要自己折腾 Python 环境。

### 安装步骤

1. 下载 Applio 的 Windows 预编译版本。
2. 解压到一个简单路径，例如：

```text
C:\Applio
```

3. 路径尽量不要包含：
   - 中文
   - 空格
   - 特殊字符

4. 双击：

```text
run-applio.bat
```

5. 不建议用“管理员身份”运行 Applio。
6. 第一次启动会准备依赖，随后通常会打开一个本地网页界面。

RTX 3070 足以应付单路 RVC v2 实时变声。

---

## 2. 安装 VB-CABLE

VB-CABLE 的作用是：

> 把 Applio 生成的女声，作为一个虚拟麦克风送给微信、Discord、QQ 等软件。

### 安装步骤

1. 下载 VB-CABLE。
2. 解压 ZIP。
3. 在 64 位 Windows 上运行：

```text
VBCABLE_Setup_x64.exe
```

4. 以管理员身份运行安装程序。
5. 安装完成后重启 Windows。

重启后会看到两个设备：

```text
CABLE Input
CABLE Output
```

### 最容易搞错的一点

它们的名字看起来是“反的”。

正确音频链路是：

```text
Applio
   ↓
CABLE Input
   ↓
[VB-CABLE 内部传输]
   ↓
CABLE Output
   ↓
Discord / 微信
```

所以：

- **Applio Output Device：CABLE Input**
- **Discord / 微信 Microphone：CABLE Output**

---

## 3. 找一个合适的 RVC v2 女声模型

你的目标不是“角色音”，而是：

> **普通话自然成年女性说话声音**

优先找满足这些条件的模型：

- RVC v2
- 女性声音
- 普通话训练素材较多
- 说话模型优先，而不是纯唱歌模型
- 训练素材干净
- 最好同时包含 `.pth` 和 `.index`

通常你会看到：

```text
ChineseFemale.pth
ChineseFemale.index
```

其中：

| 文件 | 作用 |
|---|---|
| `.pth` | 真正的 RVC 女声音色模型 |
| `.index` | 目标女声训练数据的检索特征库 |

建议优先从模型作者本人页面、Hugging Face 等来源寻找，并确保你有权使用对应模型或训练素材。

可搜索关键词：

```text
RVC v2 Chinese female
RVC Mandarin female
RVC 普通话 女声 v2
RVC speech female
```

不建议第一开始优先找：

```text
某明星 RVC
某动漫角色 RVC
```

因为这类模型的训练素材往往复杂，说普通话未必自然。

---

## 4. 把模型导入 Applio

可以建立：

```text
C:\Applio\logs\ChineseFemale\
```

然后把模型放进去：

```text
C:\Applio\logs\ChineseFemale\ChineseFemale.pth
C:\Applio\logs\ChineseFemale\ChineseFemale.index
```

随后在 Applio 中刷新模型列表。

如果 Applio 版本提供模型导入 / Download 页面，也可以直接使用界面导入。

---

## 5. 先做离线测试

不要一开始就直接折腾 Discord。

先在 Applio 普通 **Inference** 页面测试模型。

建议录一段普通话，例如：

> 你好，我现在正在测试实时语音转换。今天晚上准备出去吃饭，不知道天气怎么样。

先确认：

- 模型音色是否自然
- 中文是否清楚
- 有没有明显机器人感
- 男声底色是否严重泄漏
- 声调有没有奇怪变化

如果离线转换本身就不好听，那么实时模式通常也不会更好。

---

# 6. 第一套男声 → 女声基础参数

建议第一次从下面这一套开始。

| 参数 | 建议起始值 |
|---|---:|
| Model | **RVC v2 女声** |
| F0 Method | **RMVPE** |
| Pitch | **+8** |
| Index Rate / Search Feature Ratio | **0.50** |
| Protect | **0.33～0.40** |
| Volume Envelope | **0.4～0.5** |
| Autotune | **OFF** |
| Proposed Pitch | **OFF，第一轮先不用** |
| Clean Audio | **OFF，第一轮先不用** |
| Embedder | **ContentVec，除非模型作者指定其他** |

---

## 7. 为什么 Pitch 不建议直接 +12

`+12 semitone` 等于提高一个完整八度。

假设原始男声基频是：

```text
130 Hz
```

提高 +12 后：

```text
260 Hz
```

这对很多成年女性自然说话来说已经偏高，很容易出现：

- 太尖
- 太幼
- 动漫感
- 假女声感

而 +8 半音时：

```text
130 Hz → 约 206 Hz
```

通常更容易进入自然女性说话区域。

所以第一次建议：

```text
+8
```

然后依次测试：

```text
+6
+7
+8
+9
+10
```

不要默认 +12。

---

# 8. 中文普通话建议关闭 Autotune

日常聊天：

```text
Autotune = OFF
```

原因是中文属于声调语言。

例如：

```text
妈 mā
麻 má
马 mǎ
骂 mà
```

它们的区别高度依赖 F0 音高轨迹。

你希望 RVC 保留原始的：

```text
↗
↘
↘↗
```

而不是把它们吸到音乐音阶上。

因此：

- 普通话聊天：**Autotune OFF**
- 唱歌场景：可以另行测试

---

# 9. 进入 Applio Realtime

确定离线模型效果不错后，进入：

```text
Realtime
```

### Input Device

选择你的真实麦克风，例如：

```text
Microphone (USB Audio Device)
```

### Output Device

选择：

```text
CABLE Input (VB-Audio Virtual Cable)
```

这样 Applio 生成的女声就会进入 VB-CABLE。

---

# 10. RTX 3070 推荐实时参数

先不要追求极限低延迟。

第一套建议以稳定为主：

| Realtime 参数 | RTX 3070 起始值 |
|---|---:|
| Chunk Size | **约 160 ms** |
| Crossfade | **0.06 s** |
| Extra Conversion | **0.20～0.25 s** |
| F0 | **RMVPE** |
| Pitch | **+8** |
| Index | **0.50** |
| Protect | **0.33～0.40** |
| Volume Envelope | **0.45** |
| VAD | **先 OFF** |
| Noise Cleaning | **OFF** |
| Post Processing | **OFF** |
| Autotune | **OFF** |

原则：

```text
先稳定
↓
再减延迟
```

不要第一次就追求 50ms、80ms 这种数字。

---

# 11. Chunk Size 是什么

Applio 实时处理不是逐个采样点处理，而是：

```text
收一小块音频
↓
AI 转换
↓
输出
```

例如一个 Chunk 是 160ms。

### Chunk 越大

优点：

- 上下文更多
- F0 更稳定
- 音色更连续
- 更不容易爆音

缺点：

- 延迟更高

### Chunk 越小

优点：

- 延迟降低

缺点：

- GPU 调用更加频繁
- 容易出现：
  - 爆音
  - 断裂
  - 音色不稳定
  - F0 跳动
  - 机器人感

---

# 12. Crossfade 是什么

假设 AI 分别生成两个块：

```text
A：你今
B：天晚上
```

如果直接拼起来：

```text
A + B
```

接缝位置可能出现：

- 咔哒
- 突然断裂
- 音色跳动

Crossfade 会让两个音频块尾部和头部稍微重叠：

```text
A ↘
   ↗ B
```

所以声音会更加连续。

RTX 3070 建议先从：

```text
0.06 s
```

开始。

---

# 13. Extra Conversion 是什么

它的作用可以理解成：

> 给当前音频块增加一点前面的历史上下文。

这样 AI 在处理当前语音时，可以知道上一小段发生了什么。

好处：

- F0 更连续
- 长句更稳定
- 音色更连续
- 减少句子中突然变粗 / 变尖

代价：

- GPU 要处理更多数据
- 延迟和计算压力都会增加

RTX 3070 建议第一套：

```text
0.20～0.25 s
```

---

# 14. 监听自己的声音

如果 Applio 有：

```text
Monitor Device
```

选择：

```text
你的耳机
```

建议一定使用耳机。

不要使用音箱监听，否则可能出现：

```text
音箱播放 RVC 女声
↓
麦克风重新收到
↓
Applio 再转换
↓
再次播放
```

会造成：

- 回声
- 啸叫
- 重复转换

正确结构：

```text
Mic → Applio
             ├→ CABLE Input
             │
             └→ Headphones
```

---

# 15. Discord 设置

打开：

```text
User Settings
→ Voice & Video
```

### Input Device

选择：

```text
CABLE Output (VB-Audio Virtual Cable)
```

### Output Device

仍然选择：

```text
你的耳机
```

然后做 Discord 的 Mic Test。

如果整个链路正确，你听到的应该是：

> RVC 转换后的女声

而不是你的原始男声。

---

# 16. Discord 建议先关闭 Krisp

第一轮测试：

```text
Noise Suppression / Krisp = OFF
```

原因是 RVC 已经生成了一次完整的 AI 音频。

如果 Discord 再运行强力 AI 降噪，有时候会进一步损坏：

- 气声
- 高频
- 轻辅音
- 尾音
- 细节

如果关闭 Krisp 后环境噪声太大，再单独考虑降噪。

---

# 17. 微信设置

如果微信电脑版当前版本支持单独选择麦克风：

选择：

```text
CABLE Output
```

如果没有明显的麦克风选择项：

打开 Windows：

```text
设置
→ 系统
→ 声音
→ 输入
```

临时将：

```text
CABLE Output
```

设置为默认输入设备。

然后重新启动微信通话。

完整链路：

```text
你的男声
↓
真实麦克风
↓
Applio
↓
RVC v2
↓
女性声音
↓
CABLE Input
↓
VB-CABLE
↓
CABLE Output
↓
微信 / Discord
```

---

# 18. 开始真正调自然度

不要同时修改十个参数。

建议按照下面的顺序调整。

---

## 第一优先：Pitch

从：

```text
+8
```

开始。

### 如果听起来还是有明显男性底色

试：

```text
+9
+10
```

### 如果声音：

- 太尖
- 太幼
- 像动漫人物
- 像夹出来的假女声

降到：

```text
+7
+6
```

这一项对男 → 女效果影响非常大。

---

# 19. 第二优先：Index Rate

从：

```text
0.50
```

开始。

### 如果不像目标女性

提高：

```text
0.55
0.60
0.65
```

### 如果出现：

- 金属音
- 咬字怪
- z / c / s 模糊
- zh / ch / sh 奇怪
- 共振异常

降低：

```text
0.45
0.40
0.35
```

一般建议优先在：

```text
0.4～0.6
```

之间寻找最合适的点。

不建议一开始就拉到：

```text
0.9～1.0
```

---

# 20. Protect

中文测试时尤其关注：

```text
s
sh
x
q
ch
t
k
f
```

可以反复说：

> 其实这件事情还是需要仔细想一下。

如果出现：

- 嘶音被吞
- 辅音糊
- 呼吸声突然断
- 清辅音明显异常

可以从：

```text
0.40
```

逐渐试：

```text
0.35
0.33
0.30
```

选择最自然的一档。

---

# 21. RMVPE vs FCPE

第一选择：

```text
RMVPE
```

如果已经自然稳定：

> 不用为了参数更“高级”继续折腾。

如果你发现：

- 三声经常抽搐
- 尾音突然掉下去
- 低音突然丢失
- 偶尔突然跳一个八度
- 中文声调变化不自然

再测试：

```text
FCPE
```

建议使用完全相同的一句话做 A/B 测试，例如：

> 我本来以为今天下午天气会比较好，结果晚上突然下雨了。

不要只比较“哪个声音更亮”。

重点比较：

> **哪个中文声调更自然。**

---

# 22. 然后才开始降低延迟

初始：

```text
Chunk ≈ 160ms
Crossfade ≈ 60ms
Extra ≈ 200～250ms
```

如果完全稳定：

第一步：

```text
Chunk → 140ms
```

稳定：

```text
Chunk → 120ms
```

仍然稳定：

```text
Chunk → 100ms
```

如果出现：

- 爆音
- 噼啪声
- 句首缺失
- 声线突然改变
- 音色不连续
- GPU latency 偶尔暴涨

例如：

```text
100ms 出问题
↓
回到 120ms
```

那个位置通常就是你的 sweet spot。

---

# 23. Crossfade 调节

建议范围：

```text
0.05～0.08 s
```

先：

```text
0.06
```

如果出现：

> 一句话明显像一段一段拼起来

提高：

```text
0.07
0.08
0.10
```

如果声音已经非常顺，但是延迟感觉明显：

适当降低。

---

# 24. Extra Conversion 调节

RTX 3070 建议范围：

```text
0.20～0.30 s
```

第一套：

```text
0.25
```

如果：

- 长句中音色漂
- 一句话前后声音差别明显
- F0 连续性不好

提高到：

```text
0.30
0.35
```

如果：

- GPU 跟不上
- 延迟逐渐增加
- 实时处理开始堆积

降低：

```text
0.20
0.15
```

---

# 25. RTX 3070 最终推荐目标区间

如果模型与你的声音匹配，最终参数大概率会落在：

| 设置 | 推荐目标范围 |
|---|---:|
| RVC | **v2** |
| F0 | **RMVPE** |
| Pitch | **+7～+9** |
| Index | **0.4～0.6** |
| Protect | **0.30～0.40** |
| Volume Envelope | **0.4～0.6** |
| Chunk | **100～140ms** |
| Crossfade | **0.05～0.08s** |
| Extra | **0.15～0.30s** |
| Autotune | **OFF** |
| Clean Audio | **OFF，除非确实需要** |
| Discord Krisp | **先 OFF** |

---

# 26. 第一套推荐参数汇总

如果你现在只想复制一套参数开始试：

```text
Model: RVC v2
F0 Method: RMVPE
Pitch: +8
Index Rate: 0.50
Protect: 0.35
Volume Envelope: 0.45

Chunk Size: 160ms
Crossfade: 0.06s
Extra Conversion: 0.25s

Autotune: OFF
Clean Audio: OFF
VAD: OFF（第一轮）
Post Processing: OFF
```

### 音频设备

```text
Applio Input:
你的真实麦克风

Applio Output:
CABLE Input

Discord / 微信 Microphone:
CABLE Output

Monitor:
你的耳机
```

---

# 27. 推荐测试方法

不要测试：

```text
啊——啊——啊——
```

也不要一开始刻意模仿女性说话。

直接用自己平时正常聊天的声音连续说 20～30 秒。

例如：

> 我今天下午出去了一趟，回来以后发现外面天气还是挺热的。晚上准备吃点东西，然后可能打会游戏。其实这个声音听起来已经挺自然了，不过我还想再调整一下延迟和音高。

重点听四件事：

### ① 像不像真实女性

而不是：

> 像不像一个“变声器”。

### ② 中文辅音清不清楚

重点：

```text
zh / ch / sh
j / q / x
z / c / s
```

### ③ 中文声调有没有怪异跳动

尤其：

- 二声
- 三声
- 连续变调

### ④ 一句话中声线是否稳定

有没有突然：

- 变粗
- 变尖
- 金属化
- 断裂

---

# 28. 模型质量往往比参数更加重要

第一次不要急着自己训练模型。

建议先准备：

```text
2～3 个高质量中文普通话 RVC v2 女性模型
```

然后使用**完全相同的参数**做 A/B/C 测试。

你很可能会发现：

> 换一个真正适合你声音的模型，提升比连续调几个小时参数还大。

真正理想的模型通常具备：

- 普通话女性
- 自然聊天风格
- 非强烈播音腔
- 非纯唱歌模型
- 数据干净
- 音域适中
- 没有大量混响
- 没有背景音乐
- 没有严重压缩失真

---

# 29. 最终推荐工作流

完整系统：

```text
[真实麦克风]
       ↓
[Applio Realtime]
       ↓
[RVC v2 女声模型]
       ↓
[RMVPE F0]
       ↓
[Index Retrieval]
       ↓
[Crossfade / SOLA]
       ↓
[CABLE Input]
       ↓
[VB-CABLE]
       ↓
[CABLE Output]
       ↓
[微信 / Discord / QQ]
```

RTX 3070 的优化原则：

```text
第一阶段：
先让声音自然稳定

第二阶段：
找到正确 Pitch

第三阶段：
调 Index / Protect

第四阶段：
RMVPE 与 FCPE A/B

第五阶段：
逐渐降低 Chunk

第六阶段：
微调 Crossfade / Extra

最后：
再考虑降噪、VAD、ASIO 等高级优化
```

---

## 最简结论

对你的机器和用途，建议先从：

```text
RVC v2
RMVPE
Pitch +8
Index 0.50
Protect 0.35
Chunk 160ms
Crossfade 60ms
Extra 250ms
Autotune OFF
```

开始。

等声音完全稳定以后，再把 Chunk 从：

```text
160 → 140 → 120 → 100ms
```

逐步往下降。

对于中文男声 → 女声，最重要的优先级通常是：

```text
模型质量
>
Pitch 匹配
>
F0 稳定度
>
Index / Protect
>
实时 Chunk 参数
>
极限低延迟
```

不要为了低几十毫秒延迟，牺牲声调、咬字和自然度。
