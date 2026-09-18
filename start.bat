@echo off
cd /d "%~dp0"
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
if not exist .env (
    echo Configure .env from .env.example first.
    exit /b 1
)
python -m uvicorn app.main:app --host 127.0.0.1 --port 8010
pause
