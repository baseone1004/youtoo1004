@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo 민담·야담 - 터진 제목 모으기 (1~3분 걸립니다)
python -m pip install -q yt-dlp openai ddgs anthropic requests --upgrade
python 민담_주제뽑기.py
echo.
pause
