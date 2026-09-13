@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo 사람의 이유 - 주제 뽑기 시작 (1~3분 걸립니다)
python -m pip install -q yt-dlp openai ddgs anthropic requests --upgrade
python 주제뽑기.py
echo.
pause
