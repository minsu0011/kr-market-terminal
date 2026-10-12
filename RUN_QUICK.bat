@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" goto first_run
start "" "http://127.0.0.1:8767"
".venv\Scripts\python.exe" app.py
if errorlevel 1 goto failed
exit /b 0

:first_run
echo Run RUN.cmd first.
pause
exit /b 1

:failed
echo.
echo Server failed. Check the console output.
pause
exit /b 1
