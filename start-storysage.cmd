@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
set TIKTOKEN_CACHE_DIR=%~dp0.setup-cache\tiktoken
echo Starting StorySage text interview...
".venv\Scripts\python.exe" src\main.py --mode terminal --user_id local_demo
pause
