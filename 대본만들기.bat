@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo 사람의 이유 - 오늘 대본 만들기 (편당 2~5분 걸립니다)
python -m pip install -q openai ddgs anthropic requests --upgrade
python 대본생성.py %*
echo.
pause
