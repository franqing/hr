@echo off
rem RecruitApp standalone backend launcher (installed app\run.bat; used by desktop launcher and logon task)
rem Injects data/web/browser dirs, starts uvicorn on 127.0.0.1:8000, appends log to data\logs\app.log.
rem Idempotent: if /health on 8000 already OK (our process), exit to avoid duplicate (spec 4.6).
rem ASCII-only comments: GBK cmd mis-parses UTF-8 Chinese in batch.
setlocal
cd /d "%~dp0"
rem Portable anaconda python needs its own Library\bin on PATH to load DLLs (_sqlite3/sqlite3.dll)
rem independent of host PATH - colleague machines do not have anaconda on PATH.
set "PY_ROOT=%~dp0python"
set "PATH=%PY_ROOT%;%PY_ROOT%\DLLs;%PY_ROOT%\Library\bin;%PY_ROOT%\Library\mingw-w64\bin;%PY_ROOT%\Library\usr\bin;%PATH%"
set "DATA_DIR=%LOCALAPPDATA%\RecruitApp\data"
if not exist "%DATA_DIR%\logs" mkdir "%DATA_DIR%\logs"
set "RECRUIT_DATA_DIR=%DATA_DIR%"
set "RECRUIT_WEB_DIR=%~dp0web"
set "PLAYWRIGHT_BROWSERS_PATH=%~dp0browsers"
powershell -NoProfile -ExecutionPolicy Bypass -Command "try { (Invoke-WebRequest -UseBasicParsing -Uri http://127.0.0.1:8000/health -TimeoutSec 2).StatusCode } catch { exit 1 }" | findstr /C:"200" >nul
if not errorlevel 1 exit /b 0
"%~dp0python\pythonw.exe" -m uvicorn backend.main:app --host 127.0.0.1 --port 8000 >> "%DATA_DIR%\logs\app.log" 2>&1
endlocal
