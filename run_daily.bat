@echo off
rem Daily after-close job: archive EM pools/LHB -> update daily bars -> next-day plan
rem Keep this file ASCII-only: cmd parses it with the system codepage.
cd /d "%~dp0"
if not exist logs mkdir logs
set PYTHONIOENCODING=utf-8
echo ==================== %date% %time% >> logs\daily.log
rem Archive first: these pools expire after a few weeks
.venv\Scripts\python.exe archive_pools.py >> logs\daily.log 2>&1
.venv\Scripts\python.exe update_data.py >> logs\daily.log 2>&1
.venv\Scripts\python.exe daily_pick.py >> logs\daily.log 2>&1
