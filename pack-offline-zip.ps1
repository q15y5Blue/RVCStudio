# 离线全量包（可用分发形态）：把“内层 Inno 安装程序 + 引擎分片”打成单个 ZIP(ZIP64)。
#
# 为什么不再用单个自解压 exe：Windows 无法运行总体积超过约 4GB 的单个可执行文件。
# 实测 7z.sfx 拼出的 4.7GB 单文件在 CreateProcess 阶段即被拒绝，Win32 错误码 193
# （ERROR_BAD_EXE_FORMAT，界面提示“此应用无法在你的电脑上运行”）；这与 Inno Setup
# 单 Setup.exe 约 4.2GB 的上限是同一类限制。引擎分片合计已达 4.93GB，单文件 SFX 必然越界。
#
# 因此最终交付为单个 ZIP：解压得到一个文件夹，双击其中 RVCStudio-Setup.exe 即可，
# 安装全程离线，行为与单文件完全相同，仅多“解压一次”。ZIP 用 ZIP64，Win10/11 资源管理器
# 可直接“全部解压”；个别老系统可用 7-Zip。
$ErrorActionPreference = 'Stop'
$projectRoot = $PSScriptRoot
$dist = Join-Path $projectRoot 'dist'
$chunkDir = Join-Path $projectRoot 'offline-build\engine-chunks'
$inner = Join-Path $dist 'RVCStudio-Setup-0.3.0-offline-inner.exe'
$sevenZip = 'C:\Program Files\7-Zip\7z.exe'
$folderName = 'RVCStudio-0.3.0-offline'
$stage = Join-Path $dist $folderName
$zip = Join-Path $dist ($folderName + '.zip')
$engineSize = 4929106166L

if (-not (Test-Path -LiteralPath $inner)) { throw '缺少内层安装程序，请先编译 installer\offline.iss' }
if (-not (Test-Path -LiteralPath $sevenZip)) { throw "缺少 7-Zip：$sevenZip" }

# 1) 组装分发文件夹：固定名 setup + 4 个引擎分片（硬链接，避免重复占用 4.9GB）
Remove-Item -LiteralPath $stage -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path $stage | Out-Null
Copy-Item -LiteralPath $inner -Destination (Join-Path $stage 'RVCStudio-Setup.exe') -Force
for ($i = 1; $i -le 4; $i++) {
    $name = 'engine.bin.{0:D3}' -f $i
    New-Item -ItemType HardLink -Force -Path (Join-Path $stage $name) -Target (Join-Path $chunkDir $name) | Out-Null
}

# 2) 中文安装说明（UTF-8 BOM，保证记事本显示正常）
$readme = @'
RVC Studio 0.3.0 离线全量版 —— 安装说明
========================================

【怎么装】
1. 如果你拿到的是 RVCStudio-0.3.0-offline.zip，请先把它“全部解压”到同一个文件夹
   （右键 → 全部解压；或用 7-Zip 解压），不要在压缩包窗口里直接双击运行。
2. 解压后本文件夹里应同时有：RVCStudio-Setup.exe 和 engine.bin.001～engine.bin.004，
   这些文件必须放在一起，不能把 RVCStudio-Setup.exe 单独复制出去。
3. 双击 RVCStudio-Setup.exe，按中文向导安装即可，全程不需要联网。

【已内置】
- Applio 3.6.5 变声引擎（约 4.93GB，安装时本地合并、校验，无需下载）。
- 两把自然普通话女声：默认选中主模型 ChineseFemale（标准 RVC v2/200ep），
  在软件“① 模型与声音”页可一键切换备选 ChineseFemale_HQ（Ov2/350ep）做 A/B 对比。
- VB-CABLE 虚拟麦克风，安装时经一次管理员授权自动装好，无需另外下载。

【注意】
- 建议预留约 25GB 可用磁盘空间。
- 首次安装 VB-CABLE 驱动后可能需要重启电脑。
- 实时变声需要 NVIDIA CUDA 显卡及驱动；无需自行安装 Python 或 CUDA Toolkit。
- 应用数据位于 %LOCALAPPDATA%\RVCStudio，卸载会保留个人数据和 VB-CABLE。

【为什么是一个压缩包而不是单个 exe】
Windows 无法运行总体积超过约 4GB 的单个自解压 exe（会提示“此应用无法在你的电脑上运行”）。
因此离线版改为“一个压缩包”，解压后双击里面的安装程序，效果与单文件完全相同，仅多一步解压。
'@
[System.IO.File]::WriteAllText((Join-Path $stage 'README.txt'), $readme, (New-Object System.Text.UTF8Encoding($true)))

# 3) 校验分片合计大小（必须等于官方 ApplioV3.6.5.zip 大小）
$sum = (Get-ChildItem -LiteralPath $stage -Filter 'engine.bin.*' | Measure-Object Length -Sum).Sum
if ([int64]$sum -ne $engineSize) { throw "引擎分片合计大小异常：$sum（应为 $engineSize）" }

# 4) 打单个 ZIP（store：分片已是压缩数据；ZIP64 由 7z 自动启用）
Remove-Item -LiteralPath $zip -Force -ErrorAction SilentlyContinue
Push-Location -LiteralPath $dist
try {
    & $sevenZip a -tzip -mx=0 -mmt=on -bso0 -bsp1 $zip $folderName
    if ($LASTEXITCODE -ne 0) { throw 'ZIP 打包失败' }
} finally { Pop-Location }

# 5) 完整性测试 + 哈希侧车
& $sevenZip t $zip -bso0 -bsp1 | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'ZIP 完整性测试失败' }
$hash = (Get-FileHash -LiteralPath $zip -Algorithm SHA256).Hash.ToLower()
Set-Content -LiteralPath ($zip + '.sha256') -Value ($hash + '  ' + (Split-Path $zip -Leaf)) -Encoding ASCII
Write-Host ('DONE: ' + $zip)
Get-Item -LiteralPath $zip | Select-Object Name, @{n = 'GB'; e = { [math]::Round($_.Length / 1GB, 2) }}
