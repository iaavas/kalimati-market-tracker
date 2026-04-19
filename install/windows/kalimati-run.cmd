@echo off
REM Wrapper for Windows Task Scheduler. Edit paths if you moved the repo.
cd /d "%~dp0..\.."
".venv\Scripts\python.exe" scripts\kalimati_schedule.py %*
