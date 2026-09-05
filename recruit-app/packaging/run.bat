@echo off
rem RecruitApp 单机版后端启动器（安装版 app\run.bat；被桌面 launcher 与登录计划任务调用）
rem 注入数据/前端/浏览器目录 → 起 uvicorn（仅 127.0.0.1:8000）→ 日志追加写 data\logs\app.log。
rem 幂等：8000 上 /health 已通（本程序在跑）→ 直接退出，防双份（spec §4.6）。
setlocal
cd /d "%~dp0"
set "DATA_DIR=%LOCALAPPDATA%\RecruitApp\data"
if not exist "%DATA_DIR%\logs" mkdir "%DATA_DIR%\logs"
set "RECRUIT_DATA_DIR=%DATA_DIR%"
set "RECRUIT_WEB_DIR=%~dp0web"
set "PLAYWRIGHT_BROWSERS_PATH=%~dp0browsers"
powershell -NoProfile -ExecutionPolicy Bypass -Command "try { (Invoke-WebRequest -UseBasicParsing -Uri http://127.0.0.1:8000/health -TimeoutSec 2).StatusCode } catch { exit 1 }" | findstr /C:"200" >nul
if not errorlevel 1 exit /b 0
"%~dp0python\pythonw.exe" -m uvicorn backend.main:app --host 127.0.0.1 --port 8000 >> "%DATA_DIR%\logs\app.log" 2>&1
endlocal
