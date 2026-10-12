@echo off
setlocal
cd /d "%~dp0"
set "BUILD_PYTHON=python"
if exist ".venv\Scripts\python.exe" set "BUILD_PYTHON=.venv\Scripts\python.exe"

"%BUILD_PYTHON%" -m PyInstaller --noconfirm --clean KR_DayTrading_Terminal.spec
if errorlevel 1 goto failed
if not exist "dist\data" mkdir "dist\data"
copy /y ".env.example" "dist\.env.example" >nul
copy /y "data\external_features.json" "dist\data\external_features.json" >nul
copy /y "data\news_manual.json" "dist\data\news_manual.json" >nul
copy /y "RUN_PORTABLE.bat" "dist\RUN_PORTABLE.bat" >nul
echo Build ready: dist\KR_DayTrading_Terminal.exe
exit /b 0

:failed
echo Build failed. Install PyInstaller in the build environment first.
exit /b 1
