@echo off
rem 啟動本機操作台並開啟瀏覽器：http://127.0.0.1:8765（關閉這個視窗即停止）
cd /d "%~dp0.."
set PYTHONIOENCODING=utf-8
".venv\Scripts\python.exe" -m src.web.console --open
