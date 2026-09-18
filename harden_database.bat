@echo off
cd /d "%~dp0"
set PYTHONUTF8=1
choice /M "Create or rotate the restricted database role and update local .env"
if errorlevel 2 exit /b 0
python -m scripts.harden_database
if errorlevel 1 exit /b 1
echo Database role configured. Restart the application.
