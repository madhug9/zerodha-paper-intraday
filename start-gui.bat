@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo Missing .venv. From this folder, in PowerShell:
  echo   python -m venv .venv
  echo   .\.venv\Scripts\Activate.ps1
  echo   pip install -r requirements.txt
  exit /b 1
)

call ".venv\Scripts\activate.bat"
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
if "%GUI_HOST%"=="" set GUI_HOST=127.0.0.1
if "%GUI_PORT%"=="" set GUI_PORT=8765

REM Give uvicorn a moment, then open the local desk.
start "" cmd /c "timeout /t 2 /nobreak >nul & start http://%GUI_HOST%:%GUI_PORT%/"

".venv\Scripts\python.exe" -m uvicorn src.webapp:app --host %GUI_HOST% --port %GUI_PORT%
endlocal
