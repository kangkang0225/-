@echo off
setlocal
cd /d "%~dp0"
set PYTHONUTF8=1
echo Travel demo: http://127.0.0.1:8765
".venv\Scripts\python.exe" -m uvicorn travel_demo.app:app --host 127.0.0.1 --port 8765 --no-access-log
pause
