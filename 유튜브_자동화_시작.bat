@echo off
chcp 65001 >nul
cd /d "%~dp0"
where python >nul 2>&1
if errorlevel 1 (
  echo Python is required. Install it from https://www.python.org/downloads/ and enable Add python to PATH.
  pause
  exit /b 1
)
python 시작.py
if errorlevel 1 pause
