@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo 대본 만들기 화면을 엽니다 (http://127.0.0.1:8766)  -  이 창을 닫으면 꺼집니다
python -m pip install -q openai ddgs anthropic requests --upgrade
python 대본선택.py
pause
