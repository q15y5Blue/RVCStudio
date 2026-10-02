@chcp 65001 >nul
@echo off
cd /d "%~dp0"
echo ============================================================
echo   RVC Studio 声音训练：AISHELL-3 + 标贝 CSMSC，RVC 与 Beatrice 各一份
echo   可随时关闭窗口，重新双击会从断点继续
echo ============================================================
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0train-voices.ps1" %*
echo.
echo 已结束（退出码 %ERRORLEVEL%）。如上方有红色错误，请把 %LOCALAPPDATA%\RVCStudio\training\train-voices.log 发出来。
pause
