# 训练自己的普通话女声（RVC + Beatrice）

双击 **`一键训练声音.bat`**，会用同一份录音分别训练 RVC v2 和 Beatrice v2，默认训练两把声音，一共 4 个模型：

| 声音名 | 数据 | 许可 | 特点 |
| --- | --- | --- | --- |
| `aishell3_SSB0565` | AISHELL-3 说话人 SSB0565，约 30 分钟 | Apache-2.0，可商用 | 青年女声，语速接近日常说话（约 5.4 字/秒），音高约 231 Hz |
| `csmsc_baker` | 标贝 CSMSC / BZNSYP，从 1 万句中均匀取约 30 分钟 | **仅限非商业用途** | 录音棚品质，标准朗读腔 |

两把声音用的数据时长相同，比较才公平。训练好的模型放在 RVC Studio 数据目录的 `models\<声音名>\`：

- RVC：`<声音名>.pth` + `<声音名>.index`
- Beatrice：`checkpoint_<声音名>.pt.gz`

在 RVC Studio「① 模型与声音」→“训练好的声音”里选中一把声音，就会同时设置它的 RVC 和 Beatrice 模型，然后在「③ 实时变声」点“A/B 切换引擎”边说边比较。
脚本最后还会用同一段男声（AISHELL-3 的 SSB0710，或用 `-TestAudio` 指定你自己的录音）把所有模型各转换一次，自动打开对比试听页。

## 需要什么

- 已安装 RVC Studio：脚本直接使用它内置的 Applio 引擎，不需要另装 Python。
- NVIDIA 显卡和驱动。**GTX 10 系（如 GTX 1070）可以训练**：
  - Applio 3.6.5 自带的是 CUDA 12.8 版 PyTorch，它从 2.8 版起不再支持 GTX 10 系（Pascal）。
    脚本会检测到这一点，询问后把引擎里的 torch / torchaudio 换成同版本号的 CUDA 12.6 版（约 2.5 GB）。
    换完后 RVC Studio 的实时变声也能在这块显卡上运行；只想修这一项可以运行 `一键训练声音.bat -FixTorchOnly`。
  - GTX 10 系的 fp16 极慢，脚本自动改用 fp32 训练，Beatrice 也会关闭混合精度。
  - 8 GB 显存用 batch 8；显存更小时自动减半。
- 硬盘空间约 15 GB：标贝压缩包 2.3 GB + 解压 2.6 GB、Beatrice 训练环境约 5 GB、训练中间文件和检查点。
- 解压标贝数据需要 7-Zip 或 WinRAR；都没有时脚本会下载官方 7-Zip 免安装使用（校验 SHA-256）。
  Windows 自带的 tar 解不了这个压缩包里的音频，会悄悄产出全是 0 的文件，所以不用它。

## 要多久

时间主要花在训练上。GTX 1070 上粗略估计（未实测）：每个 RVC 模型（200 轮）约 2～4 小时，每个 Beatrice 模型（1 万步）约 2～5 小时，四个模型合计适合放一整夜。
每一步都能中断：关掉窗口后重新双击，会跳过已完成的步骤，RVC 从最近保存的轮次、Beatrice 从最近的检查点（每 2000 步）继续。

## 常用参数

```
一键训练声音.bat -Datasets aishell3            只训练 AISHELL-3
一键训练声音.bat -Engines beatrice             只训练 Beatrice
一键训练声音.bat -Speaker SSB0666              换一位 AISHELL-3 说话人（候选见下）
一键训练声音.bat -RvcEpochs 300 -BeatriceSteps 20000
一键训练声音.bat -CsmscArchive D:\下载\BZNSYP.rar   已经下载过标贝压缩包
一键训练声音.bat -CompareOnly -TestAudio D:\我的声音.wav   只用你的录音重新生成对比
一键训练声音.bat -FixTorchOnly                 只修复 GTX 10 系的引擎 PyTorch
```

下载默认走 hf-mirror.com（国内较快），失败时自动改用 huggingface.co；Python 依赖默认用清华镜像（`-PipIndex` 可改）。

## 为什么是 SSB0565

AISHELL-3 有 176 位女声，全部是朗读录音。脚本作者从 125 位北方口音、14～40 岁的女声里按录音时长取前 40 位，各测 3 句，按语调起伏、语速、录音干净度和音高打分，前几名是：

| 说话人 | 音高 | 语调起伏 | 语速 | 特点 |
| --- | --- | --- | --- | --- |
| SSB0565 | 231 Hz | 7.4 半音 | 5.4 字/秒 | 最接近日常聊天的节奏（默认） |
| SSB0666 | 216 Hz | 8.5 半音 | 4.0 字/秒 | 语调最活、录音最干净，但偏慢 |
| SSB0287 | 242 Hz | 7.7 半音 | 4.9 字/秒 | 各项均衡，略高略亮 |
| SSB0267 | 224 Hz | 8.0 半音 | 4.8 字/秒 | 各项均衡，底噪稍多 |
| SSB1828 | 192 Hz | 10.1 半音 | 3.8 字/秒 | 起伏最大，音高偏低、更成熟 |

指标只是参考，最终以耳朵为准；觉得哪位更好听，用 `-Speaker` 换掉即可。

## 文件

- `train-voices.ps1`：整个流程（显卡检测、数据、RVC、Beatrice、写入设置、对比）
- `prepare_dataset.py`：下载 AISHELL-3 单个说话人或标贝压缩包，去首尾静音、统一响度
- `train_helpers.py`：显卡自检、RVC 底模检查、训练精度、收集模型、Beatrice 配置、生成对比
- 日志：`%LOCALAPPDATA%\RVCStudio\training\train-voices.log`
