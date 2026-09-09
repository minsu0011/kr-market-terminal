@echo off
setlocal
cd /d "%~dp0"
title KR DayTrading Terminal

if exist ".venv\Scripts\python.exe" goto use_venv

where py.exe >nul 2>nul
if errorlevel 1 goto try_python
py.exe -3 launcher.py
goto result

:try_python
where python.exe >nul 2>nul
if errorlevel 1 goto try_conda
python.exe launcher.py
goto result

:try_conda
if exist "%USERPROFILE%\anaconda3\envs\py310\python.exe" goto use_anaconda_env
if exist "%USERPROFILE%\miniconda3\envs\py310\python.exe" goto use_miniconda_env
if exist "%USERPROFILE%\anaconda3\python.exe" goto use_anaconda
goto no_python

:use_venv
".venv\Scripts\python.exe" launcher.py
goto result

:use_anaconda_env
"%USERPROFILE%\anaconda3\envs\py310\python.exe" launcher.py
goto result

:use_miniconda_env
"%USERPROFILE%\miniconda3\envs\py310\python.exe" launcher.py
goto result

:use_anaconda
"%USERPROFILE%\anaconda3\python.exe" launcher.py

:result
if errorlevel 1 goto failed
exit /b 0

:no_python
echo Python 3 was not found.
echo Install Python 3.10 or use an existing Anaconda Python environment.
pause
exit /b 1

:failed
echo.
echo The launcher failed. Check launcher_error.log in this folder.
pause
exit /b 1
