@echo off
echo Stopping Avatar server...
tasklist /fi "WINDOWTITLE eq AvatarServer" /fo csv 2>nul | find "cmd.exe" >nul
if %errorlevel%==0 (
    taskkill /f /fi "WINDOWTITLE eq AvatarServer" >nul 2>&1
)
:: Also kill any uvicorn python still running on port 8765
for /f "tokens=5" %%p in ('netstat -aon ^| find ":8765" ^| find "LISTENING"') do (
    taskkill /f /pid %%p >nul 2>&1
)
echo Avatar server stopped.
timeout /t 2 /nobreak >nul
