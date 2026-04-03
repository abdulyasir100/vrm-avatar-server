@echo off
cd /d "%~dp0"

:: Launch avatar server in a minimized window (background)
start "AvatarServer" /min cmd /c ".venv\Scripts\python.exe -m uvicorn main:app --host 0.0.0.0 --port 8765 --reload --reload-dir . > server.log 2>&1"

echo ============================================
echo  Avatar server launched in background.
echo ============================================
echo.
echo  Logs:   %~dp0server.log
echo  Check:  curl http://localhost:8765/status
echo  Stop:   Run stop.bat
echo.
timeout /t 3 /nobreak >nul
