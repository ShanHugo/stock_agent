@echo off
rem Scheduled task wrapper: run scripts\<name>.py from project root, append output to logs\<name>.log
cd /d "%~dp0.."
if not exist logs mkdir logs
set PYTHONIOENCODING=utf-8
echo ===== %date:~0,10% %time% >> "logs\%1.log"
".venv\Scripts\python.exe" "scripts\%1.py" >> "logs\%1.log" 2>&1
