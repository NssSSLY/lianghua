@echo off
rem Start the local dashboard and open it in the default browser.
rem Keep this file ASCII-only: cmd parses it with the system codepage.
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
start "" http://127.0.0.1:8686
.venv\Scripts\python.exe -u dashboard\app.py
pause
