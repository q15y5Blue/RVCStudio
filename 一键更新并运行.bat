@chcp 65001 >nul
@echo off
cd /d "%~dp0"
echo ============================================================
echo   RVCStudio  从 GitHub 拉取最新代码 - 重新打包 - 部署运行
echo ============================================================
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0update-from-github.ps1" %*
echo.
echo 已结束（退出码 %ERRORLEVEL%）。如上方有红色错误，请把 update-from-github.log 发出来。
pause
