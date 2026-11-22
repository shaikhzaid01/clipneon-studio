@echo off
cd /d "%~dp0"
echo Starting ClipNeon Studio 4K...
start http://127.0.0.1:8770
.venv\Scripts\python.exe app.py
pause
