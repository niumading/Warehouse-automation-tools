@echo off
cd /d "%~dp0"
set PYTHONUTF8=1
if not exist .env (
    echo Create and configure .env and a PostgreSQL database first. See README.md.
    exit /b 1
)
choice /M "Install dependencies and run database migrations"
if errorlevel 2 exit /b 0
python -m pip install -r requirements.txt
if errorlevel 1 exit /b 1
python -m alembic upgrade head
if errorlevel 1 exit /b 1
echo Migrations complete. Demo seed is optional; see README.md.
pause
