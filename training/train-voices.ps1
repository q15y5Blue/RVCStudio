<#
====================================================================
 RVC Studio 声音训练脚本：同一份录音分别训练 RVC v2 与 Beatrice v2

 默认训练两把普通话女声，共 4 个模型：
   aishell3_SSB0565  AISHELL-3 说话人 SSB0565（Apache-2.0，可商用）
   csmsc_baker       标贝 CSMSC / BZNSYP（仅限非商业用途）
 每把声音都训练 RVC（.pth + .index）和 Beatrice（checkpoint_*.pt.gz），
 训练好的模型放进 RVC Studio 数据目录 models\<声音名>\，可在软件里直接选择；
 最后用同一段男声把所有模型各转换一次，生成对比试听页。

 需要：已安装 RVC Studio（使用它内置的 Applio 引擎）、NVIDIA 显卡。
 GTX 10 系（Pascal）也可以：脚本会自动改用 fp32 训练，并在需要时把引擎里的
 PyTorch 换成仍支持 Pascal 的 CUDA 12.6 版本（同一个 PyTorch 版本号）。

 每一步都可以中断，重新运行会从断点继续（已完成的步骤会跳过）。

 用法：
   双击 一键训练声音.bat
   powershell -File train-voices.ps1 -Datasets aishell3            # 只训练 AISHELL-3
   powershell -File train-voices.ps1 -Engines beatrice             # 只训练 Beatrice
   powershell -File train-voices.ps1 -Speaker SSB0666              # 换一位 AISHELL-3 说话人
   powershell -File train-voices.ps1 -CompareOnly -TestAudio 我的录音.wav
   一键训练声音.bat -FixTorchOnly        # 只修复 GTX 10 系的引擎 PyTorch（实时变声也需要）
====================================================================
#>
param(
    [string[]]$Datasets      = @("aishell3", "csmsc"),
    [string[]]$Engines       = @("rvc", "beatrice"),
    [string]$Speaker         = "SSB0565",
    [double]$Minutes         = 30,
    [int]$RvcEpochs          = 200,
    [int]$BeatriceSteps      = 10000,
    [string]$Runtime         = "",
    [string]$WorkDir         = "",
    [string]$CsmscArchive    = "",
    [string]$TestAudio       = "",
    [string]$HfEndpoint      = "https://hf-mirror.com",
    # pip 同时查两个源：任一个连不上或拒绝访问都不影响安装
    [string]$PipIndex        = "https://pypi.org/simple",
    [string]$PipExtraIndex   = "https://pypi.tuna.tsinghua.edu.cn/simple",
    # PyTorch 安装包默认直接从官方源下载（实测比阿里云镜像快）；填镜像地址则先试镜像
    [string]$TorchMirror     = "",
    [switch]$Yes,
    [switch]$NoConfigure,
    [switch]$CompareOnly,
    [switch]$FixTorchOnly
)

$ErrorActionPreference = "Stop"
$here = $PSScriptRoot
$repo = Split-Path $here -Parent
# 「,」分隔的写法（-Datasets aishell3,csmsc）在 bat 里会被当成一个字符串
$Datasets = @($Datasets | ForEach-Object { $_ -split "," } | ForEach-Object { $_.Trim().ToLower() } | Where-Object { $_ })
$Engines  = @($Engines  | ForEach-Object { $_ -split "," } | ForEach-Object { $_.Trim().ToLower() } | Where-Object { $_ })

if ($env:RVC_STUDIO_DATA) { $data = $env:RVC_STUDIO_DATA } else { $data = Join-Path $env:LOCALAPPDATA "RVCStudio" }
if (-not $WorkDir) { $WorkDir = Join-Path $data "training" }
New-Item -ItemType Directory -Force -Path $WorkDir | Out-Null
$log = Join-Path $WorkDir "train-voices.log"
$models = Join-Path $data "models"
$settingsFile = Join-Path $data "settings.json"

function Log($m, $color = "Gray") {
    $line = "[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $m
    Write-Host $line -ForegroundColor $color
    Add-Content -LiteralPath $log -Value $line -Encoding UTF8
}
function Step($m) { Log ""; Log "==== $m ====" "Cyan" }
function Fail($m) { Log "错误：$m" "Red"; exit 1 }
function Run($exe, [string[]]$argv, $what) {
    Log "> $what"
    & $exe @argv
    if ($LASTEXITCODE -ne 0) { Fail "$what 失败（退出码 $LASTEXITCODE），详见上方输出与 $log" }
}
# 独立 venv 用原始 PATH，避免 Applio 引擎的 DLL 混进去
function RunVenv($exe, [string[]]$argv, $what) {
    $env:PATH = $originalPath
    try { Run $exe $argv $what } finally { $env:PATH = $applioPath }
}
# PowerShell 5.1：$ErrorActionPreference=Stop 时，重定向原生程序的 stderr 会变成终止错误
function Succeeds($exe, [string[]]$argv) {
    $saved = $ErrorActionPreference; $ErrorActionPreference = "Continue"
    try { & $exe @argv *> $null; return ($LASTEXITCODE -eq 0) } catch { return $false } finally { $ErrorActionPreference = $saved }
}
# PyTorch 轮子有 2.6～2.9 GB：pip 不能断点续传，网络一卡就从头再来。
# 先由 train_helpers 断点续传下载（默认官方源；-TorchMirror 指定镜像时先试镜像）并按 pytorch.org 公布的 SHA-256 校验，再让 pip 装本地文件。
function FetchTorch($version) {
    $wheelDir = Join-Path $WorkDir "wheels"
    $argv = @($helpers, "fetch-wheels", "--index", "https://download.pytorch.org/whl/$cudaTag",
              "--pkg", "torch==$version+$cudaTag", "--pkg", "torchaudio==$version+$cudaTag", "--dest", $wheelDir)
    if ($TorchMirror) { $argv += @("--mirror", "$TorchMirror/$cudaTag") }
    Log "> 下载 PyTorch $version+$cudaTag（约 2.6～2.9 GB，可断点续传：中断后重新运行会接着下载）"
    $out = @(& $py @argv)
    if ($LASTEXITCODE -ne 0) { Fail "下载 PyTorch $version+$cudaTag 失败，重新运行会从断点继续" }
    $wheels = @($out | Where-Object { $_ -like "*.whl" })
    if ($wheels.Count -ne 2) { Fail "没有得到 torch / torchaudio 安装包" }
    return $wheels
}
$pipNet = @("--timeout", "60", "--retries", "10", "-i", $PipIndex)
if ($PipExtraIndex) { $pipNet += @("--extra-index-url", $PipExtraIndex) }
function Confirm($question) {
    if ($Yes) { return $true }
    $answer = Read-Host "$question [Y/n]"
    return ($answer -eq "" -or $answer -match "^[Yy]")
}
function VoiceName($dataset) {
    if ($dataset -eq "aishell3") { return "aishell3_$Speaker" }
    if ($dataset -eq "csmsc") { return "csmsc_baker" }
    Fail "未知数据集 $dataset（可选 aishell3 / csmsc）"
}

# ------------------------------------------------------------ 引擎与 Python
if (-not $Runtime -and (Test-Path -LiteralPath $settingsFile)) {
    try { $Runtime = (Get-Content -LiteralPath $settingsFile -Raw -Encoding UTF8 | ConvertFrom-Json).runtime } catch {}
}
if (-not $Runtime) { $Runtime = Join-Path $data "runtime\Applio-3.6.5" }
$py = Join-Path $Runtime "env\python.exe"
if (-not (Test-Path -LiteralPath $py)) { Fail "找不到 RVC Studio 内置引擎：$py。请先安装 RVC Studio，或用 -Runtime 指定 Applio 文件夹。" }

# 与 RVC Studio 相同的引擎环境：先用引擎自带的 DLL，避免被其他 Python 干扰
$envDir = Join-Path $Runtime "env"
$dllDirs = @($envDir, (Join-Path $envDir "Library\bin"), (Join-Path $envDir "Library\mingw-w64\bin"),
             (Join-Path $envDir "Library\usr\bin"), (Join-Path $envDir "Scripts"), $Runtime) | Where-Object { Test-Path -LiteralPath $_ }
$originalPath = $env:PATH
$applioPath = ($dllDirs -join ";") + ";" + $env:PATH
$env:PATH = $applioPath
Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
Remove-Item Env:PYTHONHOME -ErrorAction SilentlyContinue
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"
$env:HF_ENDPOINT = $HfEndpoint
$helpers = Join-Path $here "train_helpers.py"

Step "检测显卡"
$smi = Get-Command nvidia-smi -ErrorAction SilentlyContinue
if (-not $smi) { Fail "没有找到 nvidia-smi：训练需要 NVIDIA 显卡和已安装的显卡驱动" }
$gpuLine = (& nvidia-smi --query-gpu=name,compute_cap,memory.total --format=csv,noheader,nounits | Select-Object -First 1)
$parts = $gpuLine -split ",\s*"
$gpuName = $parts[0]; $cap = [double]$parts[1]; $vramGb = [math]::Round([double]$parts[2] / 1024, 1)
Log "显卡：$gpuName（计算能力 $cap，显存 $vramGb GB）"
if ($cap -lt 6.0) { Fail "显卡太旧（计算能力 $cap），PyTorch 已不支持" }
$pascal = $cap -lt 7.0
if ($cap -ge 10.0) { $cudaTag = "cu128" } else { $cudaTag = "cu126" }   # cu126 覆盖 Pascal～Hopper，cu128 才有 RTX 50
if ($cap -ge 8.0) { $precision = "bf16" } elseif ($pascal) { $precision = "fp32" } else { $precision = "fp16" }
if ($vramGb -ge 7) { $rvcBatch = 8; $beatriceBatch = 8 } elseif ($vramGb -ge 5) { $rvcBatch = 4; $beatriceBatch = 4 } else { $rvcBatch = 2; $beatriceBatch = 2 }
$cores = [Environment]::ProcessorCount
$workers = [math]::Max(1, [math]::Min(4, [math]::Floor($cores / 2)))
if ($pascal) { Log "GTX 10 系（Pascal）：fp16 在这代显卡上极慢，训练将使用 fp32" "Yellow" }

$check = (& $py $helpers gpu-check | Select-Object -Last 1) | ConvertFrom-Json
if (-not $check.ok) {
    $torchVer = ($check.torch -split "\+")[0]
    Log "内置引擎的 PyTorch（$($check.torch)）不能在这块显卡上运行：$($check.error)" "Yellow"
    Log "Applio 3.6.5 自带的是 CUDA 12.8 版 PyTorch，它从 2.8 版起去掉了 GTX 10 系（Pascal）的支持；" "Yellow"
    Log "同版本号的 CUDA 12.6 版（$torchVer+$cudaTag）仍然支持。替换后 RVC Studio 的实时变声也能在这块显卡上运行。" "Yellow"
    if (-not (Confirm "现在把引擎里的 torch / torchaudio 换成 $torchVer+$cudaTag 吗？（约 2.6 GB 下载）")) { Fail "已取消" }
    if (-not (Succeeds $py @("-m", "pip", "--version"))) { Run $py @("-m", "ensurepip", "--upgrade") "为引擎安装 pip" }
    $wheels = FetchTorch $torchVer
    Run $py (@("-m", "pip", "install", "--no-deps", "--force-reinstall") + $wheels) "安装 PyTorch $torchVer+$cudaTag"
    Remove-Item -LiteralPath $wheels -ErrorAction SilentlyContinue
    $check = (& $py $helpers gpu-check | Select-Object -Last 1) | ConvertFrom-Json
    if (-not $check.ok) { Fail "替换后仍无法使用显卡：$($check.error)" }
}
Log "引擎 PyTorch $($check.torch) 可以使用 $($check.name)" "Green"
if ($FixTorchOnly) { Log "完成：RVC Studio 现在可以在这块显卡上实时变声" "Green"; exit 0 }

$voices = @($Datasets | ForEach-Object { VoiceName $_ })

if (-not $CompareOnly) {
# ------------------------------------------------------------ 数据
foreach ($dataset in $Datasets) {
    $voice = VoiceName $dataset
    $dsDir = Join-Path $WorkDir "datasets\$voice"
    $wavDir = Join-Path $dsDir "wav\$voice"
    Step "准备数据：$voice"
    if ((Test-Path -LiteralPath (Join-Path $dsDir "manifest-$voice.json")) -and (Get-ChildItem -LiteralPath $wavDir -Filter *.wav -ErrorAction SilentlyContinue | Select-Object -First 1)) {
        Log "已准备，跳过（删除 $dsDir 可重新准备）"
        continue
    }
    $argv = @($(Join-Path $here "prepare_dataset.py"), "--source", $dataset, "--out", $dsDir, "--max-minutes", "$Minutes", "--endpoint", $HfEndpoint)
    if ($dataset -eq "aishell3") { $argv += @("--speaker", $Speaker) }
    if ($dataset -eq "csmsc" -and $CsmscArchive) { $argv += @("--archive", $CsmscArchive) }
    if ($dataset -eq "csmsc") { Log "标贝 CSMSC 仅限非商业用途：训练出的模型请勿用于商业或对外分发" "Yellow" }
    Run $py $argv "下载并整理 $voice"
}

# ------------------------------------------------------------ RVC
if ($Engines -contains "rvc") {
    Step "RVC 训练准备"
    Run $py @($helpers, "ensure-rvc-assets", "--runtime", $Runtime, "--endpoint", $HfEndpoint) "检查 RVC 预训练底模"
    Run $py @($helpers, "set-precision", "--runtime", $Runtime, "--value", $precision) "设置训练精度"
    foreach ($voice in $voices) {
        Step "训练 RVC：$voice（$RvcEpochs 轮，batch $rvcBatch）"
        $dest = Join-Path $models $voice
        if (Test-Path -LiteralPath (Join-Path $dest "$voice.pth")) { Log "已有 $voice.pth，跳过（删除它可重新训练）"; continue }
        $wavDir = Join-Path $WorkDir "datasets\$voice\wav\$voice"
        $logDir = Join-Path $Runtime "logs\$voice"
        Push-Location -LiteralPath $Runtime
        try {
            # 目录在步骤开始时就会建好，而且 core.py 失败也返回 0：用完成标记 + 检查真实产物
            $preDone = Join-Path $logDir ".studio-preprocessed"
            $extDone = Join-Path $logDir ".studio-extracted"
            if (-not (Test-Path -LiteralPath $preDone)) {
                Run $py @("core.py", "preprocess", "--model-name", $voice, "--dataset-path", $wavDir, "--sample-rate", "40000",
                          "--cpu-cores", "$cores", "--cut-preprocess", "Automatic", "--chunk-len", "3.0", "--overlap-len", "0.3") "切分音频"
                if (-not (Get-ChildItem -LiteralPath (Join-Path $logDir "sliced_audios") -Filter *.wav -ErrorAction SilentlyContinue | Select-Object -First 1)) { Fail "切分音频没有产出文件" }
                Set-Content -LiteralPath $preDone -Value "ok"
            }
            if (-not (Test-Path -LiteralPath $extDone)) {
                Run $py @("core.py", "extract", "--model-name", $voice, "--f0-method", "rmvpe", "--cpu-cores", "$cores",
                          "--gpu", "0", "--sample-rate", "40000", "--embedder-model", "contentvec") "提取音高与内容特征"
                if (-not (Test-Path -LiteralPath (Join-Path $logDir "filelist.txt"))) { Fail "特征提取没有完成（缺少 filelist.txt）" }
                Set-Content -LiteralPath $extDone -Value "ok"
            }
            Log "开始训练；可随时关闭窗口，重新运行会从最近保存的轮次继续" "Yellow"
            Run $py @("core.py", "train", "--model-name", $voice, "--save-every-epoch", "10", "--save-only-latest",
                      "--total-epoch", "$RvcEpochs", "--sample-rate", "40000", "--batch-size", "$rvcBatch", "--gpu", "0",
                      "--index-algorithm", "Auto") "训练 RVC"
        } finally { Pop-Location }
        Run $py @($helpers, "collect-rvc", "--runtime", $Runtime, "--model", $voice, "--dest", $dest) "复制 RVC 模型"
    }
}

# ------------------------------------------------------------ Beatrice
if ($Engines -contains "beatrice") {
    Step "Beatrice 训练环境"
    $venv = Join-Path $WorkDir "beatrice-venv"
    $vpy = Join-Path $venv "Scripts\python.exe"
    $trainer = Join-Path $WorkDir "beatrice-trainer"
    if (-not (Test-Path -LiteralPath (Join-Path $venv ".ready"))) {
        if (-not (Test-Path -LiteralPath $vpy)) { RunVenv $py @("-m", "venv", $venv) "创建独立 Python 环境" }
        # 训练器要求 torchaudio < 2.9；2.8.0 的 CUDA 12.6 版仍支持 GTX 10 系
        $wheels = FetchTorch "2.8.0"
        RunVenv $vpy (@("-m", "pip", "install") + $pipNet + $wheels) "安装 PyTorch 2.8.0+$cudaTag"
        RunVenv $vpy (@("-m", "pip", "install") + $pipNet + @("numpy", "soundfile", "tqdm", "tensorboard",
                   "pyworld==0.3.5")) "安装训练依赖"
        Remove-Item -LiteralPath $wheels -ErrorAction SilentlyContinue
        Set-Content -LiteralPath (Join-Path $venv ".ready") -Value "ok"
    }
    # 固定在 2.0.0-rc.0：与 RVC Studio 内置的 Beatrice 推理代码一致。
    # 逐个文件走普通下载地址（不用 huggingface_hub：它的 xet 传输经 hf-mirror.com 会卡在 0 字节），
    # 每个大文件按 SHA-256 校验，镜像失败自动换 huggingface.co；已下好的文件会跳过。
    Run $py @($helpers, "fetch-hf-repo", "--repo", "fierce-cats/beatrice-trainer",
              "--revision", "f34836de014b86956096878aecb8d3b17feaaa0b", "--dest", $trainer,
              "--endpoint", $HfEndpoint) "下载 beatrice-trainer 2.0.0-rc.0（2021 个文件，约 460 MB）"
    # 训练器向上查找 .git 目录来定位自己的根目录（它假定是 git clone 下来的），逐文件下载没有 .git
    New-Item -ItemType Directory -Force -Path (Join-Path $trainer ".git") | Out-Null
    if ($pascal) { $amp = 0 } else { $amp = 1 }
    foreach ($voice in $voices) {
        Step "训练 Beatrice：$voice（$BeatriceSteps 步，batch $beatriceBatch）"
        $dest = Join-Path $models $voice
        if (Test-Path -LiteralPath (Join-Path $dest "checkpoint_$voice.pt.gz")) { Log "已有 checkpoint_$voice.pt.gz，跳过（删除它可重新训练）"; continue }
        $dataDir = Join-Path $WorkDir "datasets\$voice\wav"       # 里面只有 <voice>\ 一个说话人
        $outDir = Join-Path $WorkDir "beatrice-out\$voice"
        $cfg = Join-Path $WorkDir "beatrice-$voice.json"
        Run $py @($helpers, "beatrice-config", "--trainer", $trainer, "--out", $cfg, "--amp", "$amp",
                  "--batch", "$beatriceBatch", "--steps", "$BeatriceSteps", "--workers", "$workers") "生成 Beatrice 配置"
        # 必须运行文件而不是文件夹：Windows 的 DataLoader 子进程会重新导入主模块，
        # 以文件夹（python beatrice_trainer）启动时 multiprocessing 会跳过这一步，报 Can't get attribute 'WavDataset'
        $argv = @("beatrice_trainer\__main__.py", "-d", $dataDir, "-o", $outDir, "-c", $cfg)
        if (Test-Path -LiteralPath (Join-Path $outDir "checkpoint_latest.pt.gz")) { $argv += "-r"; Log "从上次的检查点继续" }
        Log "开始训练；可随时关闭窗口，重新运行会从最近保存的检查点继续（每 2000 步保存一次）" "Yellow"
        Push-Location -LiteralPath $trainer
        try { RunVenv $vpy $argv "训练 Beatrice" } finally { Pop-Location }
        Run $py @($helpers, "collect-beatrice", "--out", $outDir, "--model", $voice, "--dest", $dest) "复制 Beatrice 模型"
    }
}

# ------------------------------------------------------------ 设置 RVC Studio
if (-not $NoConfigure) {
    Step "把第一把声音设为 RVC Studio 当前模型"
    if (Get-Process -Name RVCStudio -ErrorAction SilentlyContinue) {
        Log "RVC Studio 正在运行：请关闭后再运行本脚本，或在软件「① 模型与声音」→“训练好的声音”里手动选择" "Yellow"
    } else {
        $v = $voices[0]; $dest = Join-Path $models $v
        $argv = @($helpers, "configure-studio", "--settings", $settingsFile)
        if (Test-Path -LiteralPath (Join-Path $dest "$v.pth")) { $argv += @("--pth", (Join-Path $dest "$v.pth")) }
        if (Test-Path -LiteralPath (Join-Path $dest "$v.index")) { $argv += @("--index", (Join-Path $dest "$v.index")) }
        if (Test-Path -LiteralPath (Join-Path $dest "checkpoint_$v.pt.gz")) { $argv += @("--beatrice", (Join-Path $dest "checkpoint_$v.pt.gz")) }
        if ($argv.Count -gt 4) { Run $py $argv "写入 RVC Studio 设置" }
    }
}
}

# ------------------------------------------------------------ 对比
Step "生成对比试听"
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$cmpDir = Join-Path $WorkDir "compare\$stamp"
$argv = @($helpers, "compare", "--runtime", $Runtime, "--worker", (Join-Path $repo "studio\worker.py"),
          "--models", $models, "--voices") + $voices + @("--out", $cmpDir, "--endpoint", $HfEndpoint)
if ($TestAudio) { $argv += @("--source", (Resolve-Path -LiteralPath $TestAudio).Path) }
Run $py $argv "用同一段男声转换所有模型"
Log "完成。对比页：$(Join-Path $cmpDir 'index.html')" "Green"
Log "模型位置：$models\<声音名>\（RVC: <声音名>.pth/.index；Beatrice: checkpoint_<声音名>.pt.gz）" "Green"
Start-Process (Join-Path $cmpDir "index.html") -ErrorAction SilentlyContinue
