@echo off
chcp 65001 >nul
cd /d %~dp0
echo [微信机器人] 启动中... (Ctrl+C 停止)
python -m bot.main --config config.yaml
pause
