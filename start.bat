@echo off
echo ============================================
echo  Avatar AI Server
echo ============================================
echo.
echo  BEFORE STARTING:
echo  1. Start LM Studio (port 1234)
echo     Model: darkidol-llama-3.1-8b-instruct-1.2-uncensored
echo  2. Start Qwen3-TTS (port 9090)
echo     Run via control panel or: C:\Python312\python.exe -m chichi_speech.server
echo.
echo ============================================
echo.

cd /d "%~dp0"

if exist .venv\Scripts\activate.bat (
    call .venv\Scripts\activate.bat
) else (
    echo [WARN] No .venv found. Using system Python.
)

python -m uvicorn main:app --host 0.0.0.0 --port 8765 --reload --reload-dir .

pause
