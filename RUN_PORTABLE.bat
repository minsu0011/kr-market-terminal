@echo off
setlocal
cd /d "%~dp0"
if not exist "KR_DayTrading_Terminal.exe" goto missing
start "" "%~dp0KR_DayTrading_Terminal.exe"
echo Server address: http://127.0.0.1:8767/
exit /b 0

:missing
echo Build the portable application first.
exit /b 1
