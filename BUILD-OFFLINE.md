# 离线全量安装包构建说明（0.3.0）

目标产物是**单个压缩包**：`dist/RVCStudio-0.3.0-offline.zip`（约 4.7 GiB，ZIP64）。
用户把它“全部解压”得到一个文件夹，双击其中的 `RVCStudio-Setup.exe` 即启动中文 Inno 向导；
安装全程**不联网**，内置变声引擎、两把自然普通话女声模型与 VB-CABLE 虚拟麦克风。

解压后的文件夹布局（这些文件必须放在一起）：

```
RVCStudio-0.3.0-offline/
├─ RVCStudio-Setup.exe     # 内层 Inno 安装程序（约 127 MB，含助手/模型/驱动）
├─ engine.bin.001 … 004    # Applio 3.6.5 引擎分片（合计 4,929,106,166 字节）
└─ README.txt              # 中文安装说明
```

> **为什么不是“单个 exe”**：Windows 无法运行总体积超过约 4 GB 的单个可执行文件。
> Inno Setup 单个 Setup.exe 有约 4.2 GB 上限；改用 7-Zip 自解压模块（7z.sfx）拼出的
> 4.7 GB 单 exe 同样在 `CreateProcess` 阶段被拒绝，Win32 错误码 193
> （`ERROR_BAD_EXE_FORMAT`，界面提示“此应用无法在你的电脑上运行”）。
> 因此先用 Inno 产出约 127 MB 的“内层安装程序”，再与引擎分片一起打成**单个 ZIP**
> （`pack-offline-zip.ps1`）。除“解压一次”外，安装体验与单文件完全一致。
> 旧的 `pack-offline-sfx.ps1` 已弃用，仅在内容总量能压到 4 GB 以内时才可行。

## 一、构建环境

- Windows 10/11 x64。
- Python 3.11/3.12 x64，创建虚拟环境并安装构建依赖：
  ```powershell
  py -3.12 -m venv .build-venv312
  .\.build-venv312\Scripts\python.exe -m pip install -r requirements-build.txt
  ```
  （在被托管/注入了 `PYTHONPATH` 的终端里构建前，请先
  `Remove-Item Env:PYTHONPATH,PYTHONHOME`，否则 PyInstaller 隔离子进程可能报错。）
- 7-Zip（用于打 ZIP 与校验），默认路径 `C:\Program Files\7-Zip\7z.exe`。
  不再需要 7z 自解压模块 `7z.sfx`（>4GB 单 exe 无法运行，已弃用该方案）。
- Inno Setup 6.7.3 编译器放在 `tools\InnoSetup\ISCC.exe`（第三方工具，不入库）。

## 二、不入库的离线资产（需自行准备）

仓库不包含引擎、模型权重、驱动和编译器（见 `.gitignore`），构建前按下表放置。

| 资产 | 放置位置 | 来源 / 校验 |
|---|---|---|
| Applio 3.6.5 官方引擎 | `offline-build/engine-chunks/engine.bin.001 … 004` | 官方 `ApplioV3.6.5.zip`，大小 `4,929,106,166` 字节，SHA-256 `0d6d777a2668a6b16e83d7648f227961088a54cfcb93025f63099cd1088e1ff2`。源：`https://huggingface.co/IAHispano/Applio/resolve/main/Compiled/Windows/ApplioV3.6.5.zip`；国内可用 `hf-mirror.com` 同路径镜像。 |
| 主女声 ChineseFemale（标准 RVC v2 / 200ep） | `vendor/models/ChineseFemale/ChineseFemale.pth` 与 `ChineseFemale.index` | 自然说话风格普通话女声；参考下载：`https://hf-mirror.com/SunShineFlower/Chinesebluetoothlady_400epoch/resolve/main/Bluetoothlady.zip`，解包后按文件名归位。 |
| 备选女声 ChineseFemale_HQ（Ov2 / 350ep / 40k） | `vendor/models/ChineseFemale_HQ/ChineseFemale_HQ.pth` 与 `.index` | 同一把自然女声的高配置版本；参考下载：`https://hf-mirror.com/Meeco/MeecoRVC/resolve/main/chinesebluetooth.zip`。 |
| VB-CABLE 驱动包 | `vendor/vbcable/VBCABLE_Driver_Pack45.zip` | 来自 `https://vb-audio.com/Cable/`，SHA-256 `b950e39f01af1d04ea623c8f6d8eb9b6ea5c477c637295fabf20631c85116bfb`（donationware，注意其分发条款）。 |
| Inno Setup 编译器 | `tools/InnoSetup/ISCC.exe` | Inno Setup 6.7.3；安装包可缓存为 `tools/innosetup-6.7.3.exe`。 |

模型要求（`worker.validate_model`）：checkpoint `version == "v2"`、带 F0、含权重与
config，索引为 768 维且至少 1 条向量。两个内置模型均已通过该校验。

### 引擎分片切分（每片 1.5 GB，末片为余数，均 < 2 GB）

把校验通过的 `ApplioV3.6.5.zip` 切成 `engine.bin.001 …`：

```powershell
$src = 'C:\path\to\ApplioV3.6.5.zip'
$dst = 'offline-build\engine-chunks'
New-Item -ItemType Directory -Force -Path $dst | Out-Null
$chunk = 1500000000
$in = [IO.File]::OpenRead($src); $b = New-Object byte[] (8MB); $i = 1; $written = 0
$out = [IO.File]::Create((Join-Path $dst ('engine.bin.{0:D3}' -f $i)))
while (($n = $in.Read($b, 0, $b.Length)) -gt 0) {
  if ($written + $n -gt $chunk) {
    $take = $chunk - $written
    $out.Write($b, 0, $take); $out.Close(); $i++
    $out = [IO.File]::Create((Join-Path $dst ('engine.bin.{0:D3}' -f $i)))
    $out.Write($b, $take, $n - $take); $written = $n - $take
  } else { $out.Write($b, 0, $n); $written += $n }
}
$out.Close(); $in.Close()
```

安装时 `studio/runtime.py::reassemble_chunks` 会流式合并这些分片、按固定大小与
SHA-256 强校验，再安全解压；`studio/setup_helper.py` 安装成功后删除合并出的临时 ZIP。

## 三、一键构建

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\build-offline.ps1
```

流程：校验驱动包签名 → 单元测试 → PyInstaller 冻结 `RVCSetupHelper.exe`
（内置引擎清单、驱动、两把女声模型）与 `RVCStudio.exe` → ISCC 编译内层
`RVCStudio-Setup-0.3.0-offline-inner.exe` → 调用 `pack-offline-zip.ps1`
组装分发文件夹并打成单个 `RVCStudio-0.3.0-offline.zip`（ZIP64），自动 `7z t`
校验并生成 `.sha256`。

也可分步执行：先 `ISCC installer\offline.iss`，再
`powershell -File .\pack-offline-zip.ps1`。
（`pack-offline-sfx.ps1` 为旧的单 exe 方案，成品超过 4GB 无法运行，已弃用。）

## 四、安装期行为与磁盘占用

- 用户解压 ZIP 后，`RVCStudio-Setup.exe` 与 4 个分片同处一个文件夹；安装程序从
  自身所在目录（`{src}`）读取分片，边合并边删除分片以降低峰值占用。
- 引擎安装到 `%LOCALAPPDATA%\RVCStudio\runtime\Applio-3.6.5`，模型安装到
  `%LOCALAPPDATA%\RVCStudio\models`，并在 `settings.json` 中默认选中主女声
  （仅当用户尚未选择或原文件缺失时，重装不覆盖用户选择）。
- VB-CABLE 经一次 UAC 授权安装或复用，首次安装后可能需要重启。
- 建议预留约 25 GB 可用空间。实时变声需要 NVIDIA CUDA 显卡。

## 五、在线版（对照）

`installer/integrated.iss` + `build.ps1` 仍是旧的在线统一安装器（运行时从
Hugging Face 下载引擎、不内置模型）。离线版为本次新增，不改动在线版逻辑。
