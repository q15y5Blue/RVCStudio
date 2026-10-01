param([switch]$SkipTests)
$ErrorActionPreference = 'Stop'
$projectRoot = $PSScriptRoot
# 避免宿主注入的 PYTHONPATH / PYTHONHOME 干扰 PyInstaller 的隔离子进程
Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
Remove-Item Env:PYTHONHOME -ErrorAction SilentlyContinue
# 离线构建使用本机可用的 Python 3.12 venv（由 Applio 自带 conda Python 创建）。
$pythonPath = Join-Path $projectRoot '.build-venv312\Scripts\python.exe'
$compilerPath = Join-Path $projectRoot 'tools\InnoSetup\ISCC.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) { throw '缺少 .build-venv312，请先用可用的 Python 3.11/3.12 创建 venv 并安装 requirements-build.txt' }
if (-not (Test-Path -LiteralPath $compilerPath)) { throw '请把 Inno Setup 6 编译器安装到 tools\InnoSetup' }

# 前置检查：引擎分片与内置模型必须就位
$chunkDir = Join-Path $projectRoot 'offline-build\engine-chunks'
for ($i=1; $i -le 4; $i++) {
  $c = Join-Path $chunkDir ('engine.bin.{0:D3}' -f $i)
  if (-not (Test-Path -LiteralPath $c)) { throw "缺少引擎分片 $c，请先准备离线 payload" }
}
foreach ($m in @('vendor\models\ChineseFemale\ChineseFemale.pth',
                 'vendor\models\ChineseFemale\ChineseFemale.index',
                 'vendor\models\ChineseFemale_HQ\ChineseFemale_HQ.pth',
                 'vendor\models\ChineseFemale_HQ\ChineseFemale_HQ.index')) {
  if (-not (Test-Path -LiteralPath (Join-Path $projectRoot $m))) { throw "缺少内置模型 $m" }
}

Push-Location -LiteralPath $projectRoot
try {
    & $pythonPath tools/verify_bundle.py
    if ($LASTEXITCODE -ne 0) { throw 'Bundled driver verification failed' }
    if (-not $SkipTests) {
        & $pythonPath -m unittest discover -s tests -v
        if ($LASTEXITCODE -ne 0) { throw '自动化测试失败' }
    }
    # 安装助手：内置引擎清单、VB-CABLE 驱动包、两把女声模型
    & $pythonPath -m PyInstaller --noconfirm --clean --onefile --console --name RVCSetupHelper --distpath build/app --workpath build/pyinstaller-helper --specpath . --paths studio --add-data 'studio/engine-manifest.json;.' --add-data 'vendor/vbcable/VBCABLE_Driver_Pack45.zip;.' --add-data 'vendor/models;models' studio/setup_helper.py
    if ($LASTEXITCODE -ne 0) { throw 'Setup helper build failed' }
    # 中文图形界面
    & $pythonPath -m PyInstaller --noconfirm --clean --onefile --windowed --name RVCStudio --distpath build/app --workpath build/pyinstaller --specpath . --paths studio --add-data 'studio/worker.py;.' --add-data 'studio/settings.py;.' --add-data 'studio/audio_buffers.py;.' --add-data 'studio/routing.py;.' --add-data 'studio/formant.py;.' --add-data 'studio/engine-manifest.json;.' --add-data 'studio/USER_GUIDE.txt;.' --add-data 'studio/licenses;licenses' studio/app.py
    if ($LASTEXITCODE -ne 0) { throw 'EXE 构建失败' }
    # 先编译“小型内层 Inno 安装程序”（不含引擎分片，约 127MB）
    & $compilerPath installer/offline.iss
    if ($LASTEXITCODE -ne 0) { throw '内层离线安装程序构建失败' }
    # 再把“内层安装程序 + 引擎分片”封装为单个离线 ZIP（约 4.7GB）。
    # 注意：总体积 >4GB 无法做成“可运行的单个 exe”（Windows 加载器报错 193），故改用 ZIP。
    & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $projectRoot 'pack-offline-zip.ps1')
    if ($LASTEXITCODE -ne 0) { throw '离线 ZIP 全量包封装失败' }
} finally {
    Pop-Location
}
