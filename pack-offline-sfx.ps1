# 将“小型 Inno 内层安装程序 + 引擎分片”封装为单个 7z 自解压 exe（离线全量包）。
# 双击后：自解压到临时目录 -> 启动 Inno 向导 -> 从同目录分片本地合并引擎并部署模型/驱动。
$ErrorActionPreference = 'Stop'
$projectRoot = $PSScriptRoot
$dist = Join-Path $projectRoot 'dist'
$stage = Join-Path $projectRoot 'offline-build\sfx-stage'
$chunkDir = Join-Path $projectRoot 'offline-build\engine-chunks'
$inner = Join-Path $dist 'RVCStudio-Setup-0.3.0-offline-inner.exe'
$sfxModule = 'C:\Users\q15y5\Doubao\chats\2026-09-28\new-chat\tools\7zprog\7z.sfx'
$sevenZip = 'C:\Program Files\7-Zip\7z.exe'
$final = Join-Path $dist 'RVCStudio-Setup-0.3.0-offline.exe'
if (-not (Test-Path -LiteralPath $inner)) { throw '缺少内层安装程序，请先编译 installer\offline.iss' }
if (-not (Test-Path -LiteralPath $sfxModule)) { throw "缺少 7z SFX 模块：$sfxModule" }

# 1) 暂存目录：内层安装程序（固定名）+ 4 个引擎分片（硬链接，避免重复占用 4.9GB）
Remove-Item -LiteralPath $stage -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path $stage | Out-Null
Copy-Item -LiteralPath $inner -Destination (Join-Path $stage 'RVCStudio-Setup.exe') -Force
for ($i = 1; $i -le 4; $i++) {
    $name = 'engine.bin.{0:D3}' -f $i
    New-Item -ItemType HardLink -Path (Join-Path $stage $name) -Target (Join-Path $chunkDir $name) | Out-Null
}

# 2) SFX 配置（UTF-8；;!@Install@!UTF-8! 头已声明编码，不要加 BOM）
$configPath = Join-Path $projectRoot 'offline-build\sfx-config.txt'
$config = @'
;!@Install@!UTF-8!
Title="RVC Studio 0.3.0 离线全量安装"
BeginPrompt="即将安装 RVC Studio 0.3.0 离线全量版。\n\n已内置：变声引擎（约 4.93 GB）、两把自然普通话女声模型、VB-CABLE 虚拟麦克风。\n安装全程不需要联网，建议预留 25 GB 磁盘空间。\n\n是否继续？"
RunProgram="RVCStudio-Setup.exe"
;!@Install@!UTF-8!
'@
[System.IO.File]::WriteAllText($configPath, $config, (New-Object System.Text.UTF8Encoding($false)))

# 3) 打包 7z（分片本身已是 deflate 压缩，低压缩等级即可，省时间）
$archive = Join-Path $projectRoot 'offline-build\payload.7z'
Remove-Item -LiteralPath $archive -Force -ErrorAction SilentlyContinue
Push-Location -LiteralPath $stage
try {
    & $sevenZip a -t7z -mx=3 -mmt=on -bso0 -bsp1 $archive '*'
    if ($LASTEXITCODE -ne 0) { throw '7z 打包失败' }
} finally { Pop-Location }

# 4) 拼接 SFX 模块 + 配置 + 7z 数据，得到单 exe（copy /b 流式拼接，低内存）
Remove-Item -LiteralPath $final -Force -ErrorAction SilentlyContinue
$q = { param($p) '"' + $p + '"' }
$cmdLine = '/c copy /b ' + (& $q $sfxModule) + '+' + (& $q $configPath) + '+' + (& $q $archive) + ' ' + (& $q $final)
$cmd = Start-Process -FilePath 'cmd.exe' -ArgumentList $cmdLine -NoNewWindow -Wait -PassThru
if ($cmd.ExitCode -ne 0) { throw 'SFX 拼接失败' }

# 5) 校验与哈希
if ((Get-Item -LiteralPath $final).Length -lt 4GB) { throw '最终安装包体积异常偏小' }
$hash = (Get-FileHash -LiteralPath $final -Algorithm SHA256).Hash.ToLower()
Set-Content -LiteralPath ($final + '.sha256') -Value ($hash + '  ' + (Split-Path $final -Leaf)) -Encoding ASCII
Write-Host ('DONE: ' + $final)
Get-Item -LiteralPath $final | Select-Object Name, @{n='GB';e={[math]::Round($_.Length/1GB,2)}}
