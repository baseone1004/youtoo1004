@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo 키와 설정이 맞는지 짧은 대본 1편으로 확인합니다 (1~3분)
python -m pip install -q openai ddgs anthropic requests --upgrade
python 대본생성.py --테스트
echo.
pause
