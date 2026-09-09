@echo off
setlocal
cd /d "%~dp0"

set "PYTHON=python"
if exist ".venv\Scripts\python.exe" set "PYTHON=.venv\Scripts\python.exe"

"%PYTHON%" -m pip install --upgrade pyinstaller
if errorlevel 1 goto failed

"%PYTHON%" -m PyInstaller --noconfirm --clean KR_DayTrading_Terminal.spec
if errorlevel 1 goto failed

copy /y ".env.example" "dist\.env.example" >nul
if exist "PORTABLE_README.txt" (copy /y "PORTABLE_README.txt" "dist\README.txt" >nul) else (copy /y "..\PORTABLE_README.txt" "dist\README.txt" >nul)
for %%F in (OPENAI_SETUP.md AI_ANALYSIS_ARCHITECTURE.md API_KEY_GUIDE_KO.md NEWS_PROVIDER_SETUP.md NEWS_PIPELINE_ARCHITECTURE.md CHANGES_FOR_GPT.md NEWS_GPT_VIRTUAL_GUIDE_KO.md) do (
  if exist "%%F" copy /y "%%F" "dist\%%F" >nul
  if not exist "%%F" if exist "..\%%F" copy /y "..\%%F" "dist\%%F" >nul
)
if not exist "dist\config" mkdir "dist\config"
xcopy /e /i /y "config" "dist\config" >nul
if not exist "dist\examples" mkdir "dist\examples"
xcopy /e /i /y "examples" "dist\examples" >nul

echo.
echo Portable EXE created: dist\KR_DayTrading_Terminal.exe
pause
exit /b 0

:failed
echo.
echo Portable build failed. Read the messages above.
pause
exit /b 1
