<#
====================================================================
 RVCStudio 一键更新脚本
 作用：从 GitHub 拉取最新代码 -> 跑测试 -> 重新冻结程序 ->
       部署到本机安装目录 -> 自动启动。
 日常更新代码后，双击同目录下的“一键更新并运行.bat”即可，无需人工介入。

 用法：
   双击 一键更新并运行.bat                # 常规：仅当 studio/ 有改动才重编 GUI
   powershell -File update-from-github.ps1 -RebuildAll        # 强制重编 GUI+安装助手
   powershell -File update-from-github.ps1 -Full             # 完整重打离线安装包/4.7GB ZIP
   powershell -File update-from-github.ps1 -Mirror           # 以 GitHub 为准，硬重置本地代码
   powershell -File update-from-github.ps1 -SkipTests -NoRun # 跳过测试、不自动启动
====================================================================
#>
param(
    [string]$Branch      = "main",
    [string]$Remote      = "origin",
    [string]$InstallDir  = "D:\rvc\RVCStudio",
    [switch]$Full,
    [switch]$RebuildAll,
    [switch]$SkipTests,
    [switch]$NoRun,
    [switch]$Mirror
)

$ErrorActionPreference = "Stop"
$repo = $PSScriptRoot
$log  = Join-Path $repo "update-from-github.log"
Set-Location -LiteralPath $repo

# 宿主注入的 PYTHONPATH/PYTHONHOME 会破坏 PyInstaller 隔离子进程
Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
Remove-Item Env:PYTHONHOME -ErrorAction SilentlyContinue
# 公开仓库可匿名 fetch；禁止卡在凭据弹窗
$env:GIT_TERMINAL_PROMPT = "0"

function Log($m) {
    $line = "[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $m
    Write-Host $line
    Add-Content -LiteralPath $log -Value $line -Encoding UTF8
}
function Fail($m) {
    Write-Host "`n[错误] $m" -ForegroundColor Red
    Add-Content -LiteralPath $log -Value ("[ERROR] " + $m) -Encoding UTF8
    exit 1
}
function Run([string]$exe, [string[]]$argList, [string]$what) {
    Log ("-> " + $what)
    & $exe @argList
    if ($LASTEXITCODE -ne 0) { Fail ("失败：" + $what + "（退出码 " + $LASTEXITCODE + "）") }
}

Log "================ 开始更新 RVCStudio ================"

# ---------- 1. 定位 git ----------
$git = (Get-Command git.exe -ErrorAction SilentlyContinue).Source
if (-not $git) {
    $portable = Join-Path $env:USERPROFILE "tools\PortableGit\cmd\git.exe"
    if (Test-Path -LiteralPath $portable) { $git = $portable }
}
if (-not $git) { Fail "找不到 git。请把 PortableGit 放到 %USERPROFILE%\tools\PortableGit，或把 git 加入 PATH。" }

# ---------- 2. 定位构建 Python 与测试 Python ----------
$buildPy = Join-Path $repo ".build-venv312\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $buildPy)) { Fail "缺少构建虚拟环境 .build-venv312（含 PyInstaller）。" }
$testPy = Join-Path $env:LOCALAPPDATA "RVCStudio\runtime\Applio-3.6.5\env\python.exe"
if (-not (Test-Path -LiteralPath $testPy)) { $testPy = $buildPy }

# ---------- 3. 工作区安全检查（只拦已跟踪文件的改动，忽略未跟踪文件） ----------
$dirty = & $git -C $repo status --porcelain | Where-Object { $_ -notmatch '^\?\?' }
if ($dirty) {
    Write-Host ($dirty | Out-String)
    Fail "本地有未提交的代码改动，为避免被覆盖已停止。请先提交/暂存，或用 -Mirror 以 GitHub 为准。"
}

$oldRev = (& $git -C $repo rev-parse HEAD).Trim()
Log "当前版本 $oldRev"

# ---------- 4. 拉取最新代码 ----------
Run $git @("-C", $repo, "fetch", $Remote, $Branch) "从 $Remote 拉取 $Branch"
if ($Mirror) {
    Run $git @("-C", $repo, "reset", "--hard", "$Remote/$Branch") "按 -Mirror 硬重置到 $Remote/$Branch"
} else {
    & $git -C $repo merge --ff-only "$Remote/$Branch"
    if ($LASTEXITCODE -ne 0) {
        Fail "无法快进到 $Remote/$Branch（本地有分叉提交）。确认 GitHub 为准请改用 -Mirror；本地有未推送提交请先 push。"
    }
}
$newRev = (& $git -C $repo rev-parse HEAD).Trim()
$changed = @()
if ($oldRev -ne $newRev) {
    $changed = @(& $git -C $repo diff --name-only $oldRev $newRev)
    Log ("代码已更新：{0} -> {1}，变更 {2} 个文件" -f $oldRev.Substring(0,7), $newRev.Substring(0,7), $changed.Count)
} else {
    Log "代码已是最新，无新提交。"
}

# ---------- 5. 自动化测试 ----------
if (-not $SkipTests) {
    Run $testPy @("-m", "unittest", "discover", "-s", "tests") "运行单元测试"
} else {
    Log "已按 -SkipTests 跳过测试。"
}

# ---------- 6. 构建 ----------
$guiExe    = Join-Path $repo "build\app\RVCStudio.exe"
$helperExe = Join-Path $repo "build\app\RVCSetupHelper.exe"

$helperInputs = $changed | Where-Object { $_ -like "studio/setup_helper.py" -or $_ -like "studio/engine-manifest.json" -or $_ -like "vendor/*" }
$studioInputs = $changed | Where-Object { $_ -like "studio/*" }

$buildHelper = [bool]$helperInputs -or $RebuildAll -or $Full
$buildGui    = [bool]$studioInputs -or $RebuildAll -or $Full

if ($Full) {
    # 完整离线包：复用既有全流程（verify_bundle + 冻结 GUI/助手 + Inno + ZIP）
    $iscc = Join-Path $repo "tools\InnoSetup\ISCC.exe"
    if (-not (Test-Path -LiteralPath $iscc)) { Fail "完整打包需要 tools\InnoSetup\ISCC.exe。" }
    Run (Join-Path $repo "build-offline.ps1") @("-SkipTests") "完整构建离线安装包与 ZIP（较慢）"
    $buildHelper = $true; $buildGui = $true
}
elseif ($buildHelper -or $buildGui) {
    if ($buildHelper) {
        Run $buildPy @("-m","PyInstaller","--noconfirm","--clean","--onefile","--console",
            "--name","RVCSetupHelper","--distpath","build/app","--workpath","build/pyinstaller-helper",
            "--specpath",".","--paths","studio",
            "--add-data","studio/engine-manifest.json;.",
            "--add-data","vendor/vbcable/VBCABLE_Driver_Pack45.zip;.",
            "--add-data","vendor/models;models",
            "studio/setup_helper.py") "冻结安装助手 RVCSetupHelper.exe"
    }
    if ($buildGui) {
        Run $buildPy @("-m","PyInstaller","--noconfirm","--clean","--onefile","--windowed",
            "--name","RVCStudio","--distpath","build/app","--workpath","build/pyinstaller",
            "--specpath",".","--paths","studio",
            "--add-data","studio/worker.py;.",
            "--add-data","studio/settings.py;.",
            "--add-data","studio/audio_buffers.py;.",
            "--add-data","studio/routing.py;.",
            "--add-data","studio/formant.py;.",
            "--add-data","studio/beatrice_backend.py;.",
            "--add-data","studio/beatrice_trainer.py;.",
            "--add-data","studio/engine-manifest.json;.",
            "--add-data","studio/USER_GUIDE.txt;.",
            "--add-data","studio/licenses;licenses",
            "studio/app.py") "冻结主程序 RVCStudio.exe"
    }
} else {
    Log "没有影响程序的改动，跳过冻结。"
}

# ---------- 7. 部署到安装目录 ----------
if (-not (Test-Path -LiteralPath $InstallDir)) { Fail "安装目录不存在：$InstallDir（请先运行一次离线安装）。" }
if ($buildGui -or $buildHelper) {
    Log "停止正在运行的 RVCStudio ..."
    Get-Process -Name RVCStudio,RVCSetupHelper -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 2
    if ($buildGui) {
        if (-not (Test-Path -LiteralPath $guiExe)) { Fail "构建产物缺失：$guiExe" }
        Copy-Item -LiteralPath $guiExe -Destination (Join-Path $InstallDir "RVCStudio.exe") -Force
        Log "已部署 RVCStudio.exe"
    }
    if ($buildHelper) {
        if (Test-Path -LiteralPath $helperExe) {
            Copy-Item -LiteralPath $helperExe -Destination (Join-Path $InstallDir "RVCSetupHelper.exe") -Force
            Log "已部署 RVCSetupHelper.exe"
        }
    }
} else {
    Log "未重新构建，沿用安装目录现有程序。"
}

# ---------- 8. 启动 ----------
if (-not $NoRun) {
    Log "启动 RVCStudio ..."
    Start-Process -FilePath (Join-Path $InstallDir "RVCStudio.exe")
}
Log "================ 更新完成 ================"
if ($Full) { Log "离线全量包见 dist\ 目录（内层安装程序 + RVCStudio-*.zip）。" }
