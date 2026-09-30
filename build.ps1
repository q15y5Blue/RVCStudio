param([switch]$Release)
$ErrorActionPreference = 'Stop'
$projectRoot = $PSScriptRoot
$pythonPath = Join-Path $projectRoot '.build-venv\Scripts\python.exe'
$compilerPath = Join-Path $projectRoot 'tools\InnoSetup\ISCC.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) { throw '缺少 .build-venv，请用 Python 3.11 创建 venv 并安装 requirements-build.txt' }
if (-not (Test-Path -LiteralPath $compilerPath)) { throw '请把 Inno Setup 6 编译器安装到 tools\InnoSetup' }
Push-Location -LiteralPath $projectRoot
try {
    & $pythonPath tools/verify_bundle.py
    if ($LASTEXITCODE -ne 0) { throw 'Bundled driver verification failed' }
    & $pythonPath -m unittest discover -s tests -v
    if ($LASTEXITCODE -ne 0) { throw '自动化测试失败' }
    & $pythonPath -m PyInstaller --noconfirm --clean --onefile --console --name RVCSetupHelper --distpath build/app --workpath build/pyinstaller-helper --specpath . --paths studio --add-data 'studio/engine-manifest.json;.' --add-data 'vendor/vbcable/VBCABLE_Driver_Pack45.zip;.' studio/setup_helper.py
    if ($LASTEXITCODE -ne 0) { throw 'Setup helper build failed' }
    & $pythonPath -m PyInstaller --noconfirm --clean --onefile --windowed --name RVCStudio --distpath build/app --workpath build/pyinstaller --specpath . --paths studio --add-data 'studio/worker.py;.' --add-data 'studio/settings.py;.' --add-data 'studio/audio_buffers.py;.' --add-data 'studio/routing.py;.' --add-data 'studio/engine-manifest.json;.' --add-data 'studio/USER_GUIDE.txt;.' --add-data 'studio/licenses;licenses' studio/app.py
    if ($LASTEXITCODE -ne 0) { throw 'EXE 构建失败' }
    & $compilerPath installer/integrated.iss
    if ($LASTEXITCODE -ne 0) { throw '安装包构建失败' }
    Get-ChildItem -LiteralPath (Join-Path $projectRoot 'dist') -Filter '*.exe' | ForEach-Object {
        $hash = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLower()
        Set-Content -LiteralPath ($_.FullName + '.sha256') -Value ($hash + '  ' + $_.Name) -Encoding ASCII
    }
} finally {
    Pop-Location
}
