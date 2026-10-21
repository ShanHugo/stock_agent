@echo off
rem Scheduled task wrapper: run scripts\<name>.py from project root, append output to logs\<name>.log,
rem then record the result (state\scheduler\<name>.json) and push a Discord alert if it failed.
cd /d "%~dp0.."
if not exist logs mkdir logs
set PYTHONIOENCODING=utf-8
echo ===== %date:~0,10% %time% >> "logs\%1.log"
".venv\Scripts\python.exe" "scripts\%1.py" >> "logs\%1.log" 2>&1
set RC=%ERRORLEVEL%
echo ----- exit %RC% >> "logs\%1.log"
".venv\Scripts\python.exe" "scripts\task_status.py" %1 %RC% >> "logs\task_status.log" 2>&1
exit /b %RC%
